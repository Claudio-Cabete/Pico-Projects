from fastapi import FastAPI, Request, HTTPException
import pymysql
import yaml
from datetime import datetime
from typing import Dict, Any

app = FastAPI()

# Load configuration with error handling
try:
    with open("config.yaml") as f:
        config = yaml.safe_load(f)
    db_pass = config.get("db", {}).get("password")
    if not db_pass:
        raise ValueError("Database password not found in config.yaml")
except (FileNotFoundError, yaml.YAMLError, ValueError) as e:
    raise Exception(f"Failed to load configuration: {str(e)}")

# MariaDB configuration
db_config = {
    'host': config.get("db", {}).get("host", "localhost"),
    'user': config.get("db", {}).get("user", "homatica"),
    'password': db_pass,
    'database': config.get("db", {}).get("database", "homatica"),
    'cursorclass': pymysql.cursors.DictCursor,
    'connect_timeout': 10  # Timeout in seconds
}

def get_db_connection():
    try:
        return pymysql.connect(**db_config)
    except pymysql.MySQLError as e:
        raise Exception(f"Database connection failed: {str(e)}")

@app.post("/homatica_devices")
async def homatica_devices(request: Request):
    params = request.query_params
    method = params.get('method')
    try:
        data: Dict[str, Any] = await request.json()
    except ValueError:
        raise HTTPException(status_code=400, detail={"status": "error", "message": "Invalid JSON data"})

    utcnow = datetime.utcnow()

    try:
        with get_db_connection() as conn:
            with conn.cursor() as cursor:
                if method == 'register':
                    # Validate required fields
                    if 'mac' not in data:
                        raise HTTPException(
                            status_code=400,
                            detail={"status": "error", "message": "Missing mac"}
                        )

                    cursor.execute(
                        """
                        SELECT eid, refrsh_int, mqtt_input_topic, mqtt_output_topic, 
                               mqtt_acked_output_topic, name, device_grp 
                        FROM devices WHERE mac = %s
                        """,
                        (data['mac'],)
                    )
                    device = cursor.fetchone()

                    if device:
                        cursor.execute(
                            "UPDATE devices SET last_seen = %s WHERE eid = %s",
                            (utcnow, device['eid'])
                        )
                        cursor.execute(
                            "DELETE FROM device_input WHERE device_id = %s AND ioid < %s",
                            (device['eid'], '99')
                        )
                        results = {
                            'status': '200 Ok',
                            'results': {
                                'device_grp': device['device_grp'],
                                'name': device['name'],
                                'device_id': device['eid'],
                                'refrsh_int': device['refrsh_int'],
                                'mqtt_input_topic': device['mqtt_input_topic'],
                                'mqtt_output_topic': device['mqtt_output_topic'],
                                'mqtt_acked_output_topic': device['mqtt_acked_output_topic']
                            }
                        }
                    else:
                        # Validate optional fields with defaults
                        cursor.execute(
                            """
                            INSERT INTO devices (mac, ip, name, device_type, updated, last_seen, refrsh_int)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (
                                data['mac'],
                                data.get('ip', ''),
                                data.get('name', ''),
                                data.get('device_type', ''),
                                utcnow,
                                utcnow,
                                1
                            )
                        )
                        cursor.execute("SELECT LAST_INSERT_ID() AS eid")
                        device_id = cursor.fetchone()['eid']

                        cursor.execute(
                            """
                            INSERT INTO device_io_config (device_id, ioid, io_name, io_description, io_type, io_mode, updated)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (device_id, '25', 'LED', 'LED', 'LED', 'OUT', utcnow)
                        )
                        cursor.execute(
                            """
                            INSERT INTO device_io_config (device_id, ioid, io_name, io_description, io_type, io_mode, updated)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            """,
                            (device_id, '4', 'Internal Temperature', 'Internal Temperature', 'ADC', 'IN', utcnow)
                        )
                        results = {
                            'status': '200 Ok',
                            'results': {
                                'device_grp': '',
                                'name': data.get('name', ''),
                                'device_id': device_id,
                                'refrsh_int': 1,
                                'mqtt_input_topic': '',
                                'mqtt_output_topic': '',
                                'mqtt_acked_output_topic': ''
                            }
                        }

                    conn.commit()
                    return results

                elif method == 'get_device_io_conf':
                    # Validate required fields
                    if 'device_id' not in data and 'device_name' not in data:
                        raise HTTPException(
                            status_code=400,
                            detail={"status": "error", "message": "Missing device_id or device_name"}
                        )

                    query = """
                        SELECT dic.* FROM device_io_config dic
                        JOIN devices d ON (d.eid = dic.device_id)
                        WHERE dic.eid > 0
                    """
                    params = []
                    if 'device_id' in data:
                        query += " AND dic.device_id = %s"
                        params.append(data['device_id'])
                    if 'device_name' in data:
                        query += " AND d.name = %s"
                        params.append(data['device_name'])

                    cursor.execute(query, params)
                    io_conf = {
                        row['ioid']: {
                            'ioid': row['ioid'], 
                            'io_name': row['io_name'],
                            'io_type': row['io_type'], 
                            'io_mode': row['io_mode']
                        } for row in cursor.fetchall()
                    }
                    results = {
                        'status': '200 Ok',
                        'results': {'io_conf': io_conf}
                    }
                    if 'device_name' in data:
                        cursor.execute(query, params)  # Re-execute for device_io_config
                        results['results']['device_io_config'] = [
                            {k: v for k, v in row.items()} for row in cursor.fetchall()
                        ]
                    return results

                elif method == 'get_devices':
                    cursor.execute("SELECT * FROM devices")
                    devices = cursor.fetchall()
                    return {"status": "200 Ok", "devices": devices}
                else:
                    raise HTTPException(
                        status_code=400,
                        detail={"status": "error", "message": f"Invalid method: {method}"}
                    )

    except pymysql.MySQLError as e:
        raise HTTPException(status_code=500, detail={"status": "error", "message": f"Database error: {str(e)}"})
    except ValueError as e:
        raise HTTPException(status_code=400, detail={"status": "error", "message": f"Invalid data: {str(e)}"})