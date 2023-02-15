from flask import Flask, request, jsonify
from flask_cors import CORS
import psycopg2  # todo - use sqlalchemy instead? LK: There's also flask_sqlalchemy
import json
from configparser import ConfigParser
import re


def create_app(config='database.ini'):
    """
    Create the flask app.

    :return: flask app
    """
    app = Flask(__name__, static_url_path="", static_folder='../static')

    # Load flask config
    if app.env == 'development':
        app.config.from_object('xi2_xiview_loader.config.DevelopmentConfig')
    else:
        app.config.from_object('xi2_xiview_loader.config.ProductionConfig')
        try:
            app.config.from_envvar('XI2XIVIEWLOADER_SETTINGS')
        except (FileNotFoundError, RuntimeError):
            ...

    # add CORS header
    CORS(app)

    # CORS(app, resources={
    #     r"/get_data": {
    #         "origins": "*",
    #         "headers": app.config['CORS_HEADERS']
    #     }
    # })

    # https://www.postgresqltutorial.com/postgresql-python/connect/
    def parse_database_info(filename, section='postgresql'):
        # create a parser
        parser = ConfigParser()
        # read config file
        parser.read(filename)

        # get section, default to postgresql
        db = {}
        if parser.has_section(section):
            params = parser.items(section)
            for param in params:
                db[param[0]] = param[1]
        else:
            raise Exception('Section {0} not found in the {1} file'.format(section, filename))

        return db

    # read connection information
    db_info = parse_database_info(config)

    @app.route('/get_data', methods=['GET'])
    def get_data():
        uuid = request.args.get('uuid')  # uuid of search
        # quit if uuid contains char that isn't alphanumeric, comma or hyphen
        if uuid is None or not re.match(r'^[a-zA-Z0-9,-]+$', uuid):
            return jsonify({"error": "Invalid id(s)"}), 400

        # return json.dumps(get_data_object(uuid)) # think this will be more efficient as it doesn't pretty print
        return jsonify(get_data_object(uuid))

    @app.route('/get_peaklist', methods=['GET'])
    def get_peaklist():
        uuid = request.args.get('uuid')
        return jsonify(get_peaklist_object(uuid))

    @app.route('/save_layout', methods=['POST'])
    def save_layout():
        uuid = request.form['uuid']
        layout = request.form['layout']
        description = request.form['name']

        try:
            # connect to the PostgreSQL server
            print('Connecting to the PostgreSQL database...')
            conn = psycopg2.connect(**db_info)

            # create a cursor
            cur = conn.cursor()

            sql = "INSERT INTO layout (resultset_id, layout, description) VALUES (%s, %s, %s)"

            cur.execute(sql, [uuid, layout, description])
            conn.commit()

            print("finished")
            # close the communication with the PostgreSQL
            cur.close()
            return "Layout saved!"
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
            return "Database error:\n" + str(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')

    @app.route('/load_layout', methods=['POST'])
    def load_layout():
        # actually returns all different layouts available
        uuid = request.form['uuid']

        try:
            # connect to the PostgreSQL server
            print('Connecting to the PostgreSQL database...')
            conn = psycopg2.connect(**db_info)

            # create a cursor
            cur = conn.cursor()

            sql = """SELECT t1.layout AS layout, t1.description AS name FROM layout AS t1 
                  WHERE t1.resultset_id = %s AND t1.time_saved IN 
                  (SELECT max(t1.time_saved) FROM layout AS t1  WHERE t1.resultset_id = %s GROUP BY t1.description);"""
            # sql = """SELECT t1.description, t1.layout FROM layout AS t1
            #     WHERE t1.resultset_id = %s ORDER BY t1.time_saved desc LIMIT 1"""
            cur.execute(sql, [uuid, uuid])
            layouts = cur.fetchall()
            data = {}
            # xinet_layout = {
            #     "name": data[0],
            #     "layout": data[1]
            # }
            for layout in layouts:
                data[str(layout[1])] = layout[0]

            print("finished")
            # close the communication with the PostgreSQL
            cur.close()
            return jsonify(data)
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
            return "Database error:\n" + str(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')

    @app.route('/network.html', methods=['GET'])
    def network():
        # uuid = request.args.get('uuid')
        return app.send_static_file('network.html')

    def get_data_object(uuid):
        """ Connect to the PostgreSQL database server """
        conn = None
        data = {}
        try:
            # connect to the PostgreSQL server
            print('Connecting to the PostgreSQL database...')
            conn = psycopg2.connect(**db_info)

            # create a cursor
            cur = conn.cursor()

            # i see... multiple return types, that's kind of cool,
            # maybe a bit confusing the way I've used it here
            data["sid"] = uuid
            data["resultset"], data["searches"] = get_resultset_search_metadata(cur, uuid)
            data["matches"], peptide_clause = get_matches(cur, uuid, data["resultset"]["mainscore"])
            data["peptides"], protein_clause = get_peptides(cur, peptide_clause)
            data["proteins"] = get_proteins(cur, protein_clause)
            data["xiNETLayout"] = get_layout(cur, uuid)

            print("finished")
            # close the communication with the PostgreSQL
            cur.close()
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')
            return data

    def get_peaklist_object(spectrum_uuid):
        """ Connect to the PostgreSQL database server """
        conn = None
        data = {}
        try:
            # connect to the PostgreSQL server
            print('Connecting to the PostgreSQL database...')
            conn = psycopg2.connect(**db_info)

            # create a cursor
            cur = conn.cursor()

            sql = "SELECT intensity, mz FROM spectrumpeaks WHERE id = %s"

            cur.execute(sql, [spectrum_uuid])
            data = cur.fetchall()[0]
            print("finished")
            # close the communication with the PostgreSQL
            cur.close()
        except (Exception, psycopg2.DatabaseError) as error:
            print(error)
        finally:
            if conn is not None:
                conn.close()
                print('Database connection closed.')
            return data

    return app


def get_resultset_search_metadata(cur, uuid):
    sql = """
                SELECT rs.name, rs.note, rs.config, rs.main_score, rst.name,
                      s.id, s.name, s.config, s.note
                 FROM resultset AS rs
                  LEFT JOIN resultsettype AS rst ON (rs.rstype_id = rst.id)
                  LEFT JOIN ResultSearch AS result_search ON (rs.id = result_search.resultset_id)
                  LEFT JOIN Search AS s ON (result_search.search_id = s.id)
                WHERE rs.id = %s
                           """
    cur.execute(sql, [uuid])
    resultset_meta_cur = cur.fetchall()
    first_row = resultset_meta_cur[0]
    # todo resultset.config in db, column is text but value is json
    resultset_meta = {
        "name": first_row[0],
        "note": first_row[1],
        # "config": json.loads(first_row[2]),
        "config": first_row[2],
        "mainscore": first_row[3],
        "resultsettype": first_row[4]
    }
    searches = {}
    for search_row in resultset_meta_cur:
        search = {
            "id": search_row[5],
            "name": search_row[6],
            "config": json.loads(search_row[7]),
            # "note": search_row[8]
        }
        searches[search["id"]] = search
    return resultset_meta, searches


def get_matches(cur, uuid, main_score_index):
    # todo - the join to matchedspectrum for cleavable crosslinker - needs a GROUP BY match_id?'
    sql = """SELECT m.id, m.pep1_id, m.pep2_id, 
                    CASE WHEN rm.site1 IS NOT NULL THEN rm.site1 ELSE m.site1 END, 
                    CASE WHEN rm.site2 IS NOT NULL THEN rm.site2 ELSE m.site2 END, 
                    rm.scores[%s], m.crosslinker_id,
                    m.search_id, m.calc_mass, m.assumed_prec_charge, m.assumed_prec_mz,
                    ms.spectrum_id
                FROM ResultMatch AS rm
                    JOIN match AS m ON rm.match_id = m.id
                    JOIN matchedspectrum as ms ON rm.match_id = ms.match_id
                    WHERE rm.resultset_id = %s AND m.site1 >0 AND m.site2 >0
                    AND rm.top_ranking = TRUE;"""

    cur.execute(sql, [main_score_index, uuid])
    matches = []
    search_peptide_ids = {}
    while True:
        match_rows = cur.fetchmany(5000)
        if not match_rows:
            break

        for match_row in match_rows:
            peptide1_id = match_row[1]
            peptide2_id = match_row[2]
            search_id = match_row[7]
            match = {
                "id": match_row[0],
                "pi1": peptide1_id,
                "pi2": peptide2_id,
                "s1": match_row[3],
                "s2": match_row[4],
                "sc": match_row[5],
                "cl": match_row[6],
                "si": search_id,
                "cm": match_row[8],
                "pc_c": match_row[9],
                "pc_mz": match_row[10],
                "sp_id": match_row[11]
            }
            if search_id in search_peptide_ids:
                peptide_ids = search_peptide_ids[search_id]
            else:
                peptide_ids = set()
                search_peptide_ids[search_id] = peptide_ids

            peptide_ids.add(peptide1_id)
            if peptide2_id is not None:
                peptide_ids.add(peptide2_id)

            matches.append(match)

    # create sql clause that selects peptides by id and resultset
    # (search_id = a AND id in(x,y,z)) OR (search_id = b AND (...)) OR ...
    first_search = True
    peptide_clause = "("
    for k, v in search_peptide_ids.items():
        if first_search:
            first_search = False
        else:
            peptide_clause += " OR "
        peptide_clause += "(mp.search_id = '" + str(search_id) + "' AND mp.id IN ("
        # print("rs:" + str(k))
        first_pep_id = True
        for pep_id in v:
            # print("pep:" + str(pep_id))
            if first_pep_id:
                first_pep_id = False
            else:
                peptide_clause += ","
            peptide_clause += str(pep_id)
        peptide_clause += "))"
    peptide_clause += ")"

    return matches, peptide_clause


def get_peptides(cur, peptide_clause):
    if peptide_clause != "()":
        sql = """SELECT mp.id, mp.search_id AS search_uuid,
                                mp.sequence AS sequence,
                                array_agg(pp.protein_id) AS proteins,
                                array_agg(pp.start) AS positions
                                    FROM modifiedpeptide AS mp
                                    JOIN peptideposition AS pp
                                    ON mp.id = pp.mod_pep_id AND mp.search_id = pp.search_id
                                WHERE """ + peptide_clause + """ GROUP BY mp.id, mp.search_id, mp.sequence
                               """
        # print(sql);
        cur.execute(sql)
        peptides = []
        search_protein_ids = {}
        while True:
            peptide_rows = cur.fetchmany(5000)
            if not peptide_rows:
                break
            for peptide_row in peptide_rows:
                search_id = peptide_row[1]
                prots = peptide_row[3]
                peptide = {
                    "id": peptide_row[0],
                    "seq_mods": peptide_row[2],
                    "prt": prots,
                    "pos": peptide_row[4]
                }
                if search_id in search_protein_ids:
                    protein_ids = search_protein_ids[search_id]
                else:
                    protein_ids = set()
                    search_protein_ids[search_id] = protein_ids

                for prot in prots:
                    protein_ids.add(prot)

                peptides.append(peptide)

        # create sql clause that selects proteins by id and resultset
        # (search_id = a AND id in(x,y,z)) OR (search_id = b AND (...)) OR ...
        first_search = True
        protein_clause = "("
        for k, v in search_protein_ids.items():
            if first_search:
                first_search = False
            else:
                protein_clause += " OR "
            protein_clause += "(search_id = '" + str(search_id) + "' AND id IN ("
            first_prot_id = True
            for prot_id in v:
                if first_prot_id:
                    first_prot_id = False
                else:
                    protein_clause += ","
                protein_clause += str(prot_id)
            protein_clause += "))"

        return peptides, protein_clause


def get_proteins(cur, protein_clause):
    if protein_clause != "()":
        sql = """SELECT id, name, accession, sequence, search_id, is_decoy FROM protein
                                WHERE """ + protein_clause + """)
                                """
        # print(sql);
        cur.execute(sql)
        protein_rows = cur.fetchall()
        proteins = []
        for protein_row in protein_rows:
            protein = {
                "id": protein_row[0],
                "name": protein_row[1],
                "accession": protein_row[2],
                "sequence": protein_row[3],
                "search_id": protein_row[4],
                "is_decoy": protein_row[5]
            }
            proteins.append(protein)
        return proteins


def get_layout(cur, uuid):
    sql = """SELECT t1.description, t1.layout FROM layout AS t1 
        WHERE t1.resultset_id = %s ORDER BY t1.time_saved DESC LIMIT 1"""
    cur.execute(sql, [uuid])
    data = cur.fetchall()
    if data:
        xinet_layout = {
            "name": data[0][0],
            "layout": data[0][1]
        }
        return xinet_layout
