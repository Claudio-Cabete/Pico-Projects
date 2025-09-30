"""
Created by Claudio Cabete on 07/06/2025.
This code boots a Pico W device, connects it to Wi-Fi,
registers it with a central API,
configures GPIOs based on server-provided I/O definitions,
reads sensor inputs, applies output commands received via MQTT,
and publishes its state back to the MQTT broker.
It also supports over-the-air (OTA) updates and device resets via MQTT.
It functions as multipurpose wireless automation device
to leverage the PICO multiple IN/OUT pins
"""
import rp2
import network
import urequests
import ujson
import ubinascii
import utime
import ntptime
from secrets import secrets
from mqttinf import mqttinf
from apihosts import apihosts
import machine as m
from onewire import OneWire as ow
from ds18x20 import DS18X20 as ds
import gc
from umqtt.simple import MQTTClient
from aht20 import AHT20


wdt = m.WDT(timeout=8387)

"""
class DummyWDT:
    def feed(self):
        pass  # This does nothing, just a placeholder
wdt = DummyWDT()
"""

rp2.country('US')

args = {
    'name': 'I need a name',
    'device_type': 'pico_w',
    'ip': '',
    'mac': '',
    'iscon': False,
    'isreged': False,
    'hasioconf': False,
    'printerr': True,
    'printdebug': None,
    'device_id': None,
    'mqtt_input_topic': '',
    'mqtt_output_topic': '',
    'input_vals': {},
    'prev_outputs': {},
    'logerrors': False
}

led_0 = m.Pin("LED", m.Pin.OUT)
MQTT_BROKER = mqttinf['mqtt_broker']
MQTT_PORT = 1883
MQTT_USER = mqttinf['mqtt_user']
MQTT_PASSWORD = mqttinf['mqtt_password']
client = None  # Global MQTT client variable
wlan = None  # Global WLAN variable

io_conf = {}
device_outputs = {}
ds18x20_devices = {}
gpio_initialized = {}
aht20_devices = {}

def log_error(message):
    try:
        if args['logerrors']:
            with open('error.log', 'a') as f:
                f.write(f"{utime.localtime()}: {message}\n")
    except:
        pass

def connect(args):
    global wlan
    wlan = network.WLAN(network.STA_IF)
    wlan.active(False)
    utime.sleep(0.1)
    wlan.active(True)
    wlan.config(pm=0)
    args['mac'] = ubinascii.hexlify(wlan.config('mac'), ':').decode()
    args['iscon'] = False
    for ssid in secrets:
        print(f"Trying to connect to {ssid}...")
        try:
            wlan.connect(ssid, secrets[ssid])
            for i in range(5):
                wdt.feed()
                if wlan.isconnected():
                    temp = wlan.ifconfig()
                    args['ip'] = temp[0]
                    args['ssid'] = ssid
                    args['iscon'] = True
                    print(f"Connected, IP: {args['ip']}")
                    led_0.on()
                    return args
                print(f"Waiting for connection to {ssid}... {i}")
                utime.sleep(1)
            wlan.disconnect()
        except Exception as e:
            print(f"Error connecting to {ssid}: {e}")
            log_error(f"WiFi connection error for {ssid}: {e}")
    print("Failed to connect")
    log_error("WiFi connection failed")
    return args

def mqtt_callback(topic, msg):
    global device_outputs, client
    try:
        #print(f"Received MQTT message on {topic.decode()}")
        if topic.decode() == args['mqtt_output_topic']:
            data = ujson.loads(msg.decode())
            if 'device_outputs' in data:
                print(f"Received MQTT message on {topic.decode()} {data}")
                device_outputs = data['device_outputs']
                apply_outputs(args, device_outputs)
                args['prev_outputs'] = device_outputs
                
                if client is not None:
                    client.publish(
                        args['mqtt_acked_output_topic'],
                        ujson.dumps({'acked_outputs': device_outputs}),
                        retain=True
                    )
                    print(f"Acked outputs to {args['mqtt_acked_output_topic']}: {device_outputs}")
                else:
                    log_error("MQTT client not initialized for publishing device outputs")
                    
            if data.get("mac") == args['mac'] and data.get('update_file') and data.get('filename') and data.get('file_content'):
                print(f"OTA Update Received.")
                wdt.feed()
                gc.collect()
                filename = data['filename']
                content = data['file_content']
                # Validate filename (basic security)
                if not filename or not filename.endswith('.py'):
                    log_error(f"Invalid filename: {filename}")
                    if client is not None:
                        client.publish(args['mqtt_output_topic'] + '/update_status', f"failed: Invalid filename {filename}")
                    else:
                        log_error("MQTT client not initialized for publishing update status")
                    return
                try:
                    import uos
                    """
                    # Backup existing file if it exists
                    if filename in uos.listdir():
                        backup_name = filename.replace('.py', '_backup.py')
                        uos.rename(filename, backup_name)
                        print(f"Backed up {filename} to {backup_name}")
                    """
                    # Write new file
                    with open(filename, 'w') as f:
                        f.write(content)
                    print(f"Updated {filename} successfully")
                    if client is not None:
                        client.publish(args['mqtt_output_topic'] + '/update_status', "success")
                    else:
                        log_error("MQTT client not initialized for publishing update status")
                    # Reset if updating main.py
                    if filename == 'main.py':
                        print('Firmware was updated. Resetting now')
                        m.reset()
                except Exception as e:
                    log_error(f"Failed to update {filename}: {e}")
                    if client is not None:
                        client.publish(args['mqtt_output_topic'] + '/update_status', f"failed: {e}")
                    else:
                        log_error("MQTT client not initialized for publishing update failure")
            if 'reset' in data and data['reset'] and 'mac' in data and data['mac'] == args['mac']:
                print("Resetting Pico due to matching MAC...")
                m.reset()
    except Exception as e:
        log_error(f"MQTT callback error: {e}")
        if args['printerr']:
            print(f"MQTT callback error: {e}")

def mqtt_reconnect(args):
    global client
    if client is not None:
        try:
            client.disconnect()
        except:
            pass
        client = None
    client_id = args['mac'].replace(':', '') if args['mac'] else ubinascii.hexlify(m.unique_id()).decode()
    for attempt in range(3):
        try:
            client = MQTTClient(
                client_id=client_id,
                server=MQTT_BROKER,
                port=MQTT_PORT,
                user=MQTT_USER,
                password=MQTT_PASSWORD,
                keepalive=30
            )
            client.set_callback(mqtt_callback)
            client.set_last_will(args['mqtt_input_topic'] + '/availability', "offline", retain=True, qos=0)
            client.connect()
            client.subscribe(args['mqtt_output_topic'])
            client.publish(args['mqtt_input_topic'] + '/availability', "online", retain=True)
            print(f"MQTT client reconnected with ID: {client_id}")
            return True
        except Exception as e:
            log_error(f"MQTT reconnect attempt {attempt+1} failed: {e}")
            utime.sleep(2 ** attempt)  # Exponential backoff: 1s, 2s, 4s
    print("MQTT reconnect failed after retries")
    return False

def api_err_handler(args):
    error = str(args['err'])
    log_error(f"API error: {error}")
    if args['printerr']:
        print(f"api_err_handler: {error}")
    return error

def register(args):
    header = {'Accept': 'application/json', 'Content-Type': 'application/json', 'charset': 'utf8'}
    results = {'status': False,'args':args}
    for i in range(2):
        if results['status']:
            break
        if i == 1:
            args = connect(args)
        for host in apihosts:
            wdt.feed()
            apiurl = f"http://{host}:8888/homatica_devices?method=register"
            try:
                gc.collect()
                print(f"Registration payload: {args}")
                print(f"Free memory before register: {gc.mem_free()}")
                start_time = utime.ticks_ms()
                response = urequests.post(apiurl, headers=header, json=args, timeout=5)
                print(f"Register request took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                if response.status_code != 200:
                    if args['printerr']:
                        print(f"Error with register {apiurl}")
                else:
                    
                    tmp = response.json()
                    print(f"Registration Response: {tmp}")
                    
                    if 'status' in tmp and tmp['status'] == '200 Ok':
                        args['device_id'] = tmp['results']['device_id']
                        args['device_name'] = tmp['results']['name']
                        args['name'] = tmp['results']['name']
                        args['device_grp'] = tmp['results']['device_grp']
                        args['refrsh_int'] = tmp['results']['refrsh_int']
                        args['mqtt_input_topic'] = tmp['results'].get('mqtt_input_topic', '')
                        args['mqtt_output_topic'] = tmp['results'].get('mqtt_output_topic', '')
                        args['mqtt_acked_output_topic'] = tmp['results'].get('mqtt_acked_output_topic', '')
                        args['isreged'] = True
                        results['args'] = args
                        results['api_error'] = False
                        results['status'] = True
                        break
            except Exception as err:
                args['err'] = err
                results['api_error'] = api_err_handler(args)
                if args['printerr']:
                    print(f"Error with register: {err} {apiurl}")
            finally:
                if 'response' in locals():
                    response.close()
            gc.collect()
            print(f"Free memory after register: {gc.mem_free()}")
    if not results['status']:
        log_error("Registration failed")
    return results

def init_gpio_ports(args, io_conf):
    global ds18x20_devices, gpio_initialized, aht20_devices
    for ioid in io_conf:
        if ioid in gpio_initialized:
            continue
        tmp = io_conf[ioid]
        try:
            if tmp['io_mode'] == 'OUT':
                if tmp['io_type'] in ['switch', 'switch_inv', 'LED']:
                    pin = m.Pin(int(ioid), m.Pin.OUT)
                    if tmp['io_type'] == 'switch_inv':
                        pin.on()
                    else:
                        pin.off()
                    print(f"Initialized {tmp['io_type']} on pin {ioid} as OUTPUT")
                elif tmp['io_type'] == 'PWM':
                    pwm = m.PWM(m.Pin(int(ioid)))
                    pwm.freq(1000)
                    pwm.duty_u16(0)
                    print(f"Initialized PWM on pin {ioid}")
            elif tmp['io_mode'] == 'IN':
                if tmp['io_type'] == 'ADC':
                    m.ADC(int(ioid))
                    print(f"Initialized ADC on pin {ioid}")
                elif tmp['io_type'] == 'DS18x20':
                    pin = m.Pin(int(ioid), m.Pin.IN, m.Pin.PULL_UP)
                    onew = ow(pin)
                    dsd = ds(onew)
                    devices = dsd.scan()
                    print(f"Scanned DS18x20 on pin {ioid}: {devices}")
                    if devices:
                        ds18x20_devices[ioid] = (dsd, devices[0])
                    else:
                        log_error(f"No DS18x20 devices found on pin {ioid}")
                    print(f"Initialized DS18x20 on pin {ioid}")
                elif tmp['io_type'] == 'AHT20':
                    try:
                        pinmap = ujson.loads(ioid)
                        scl = int(pinmap['SCL'])
                        sda = int(pinmap['SDA'])
                        i2c = m.I2C(0, scl=m.Pin(scl), sda=m.Pin(sda))
                        aht20_devices[ioid] = AHT20(i2c)
                        print(f"Initialized AHT20 on SCL={scl}, SDA={sda}")
                    except Exception as e:
                        log_error(f"AHT20 init error for {ioid}: {e}")
                        if args.get('printerr'):
                            print(f"AHT20 init error for {ioid}: {e}")

            gpio_initialized[ioid] = True
        except Exception as e:
            log_error(f"GPIO init error for pin {ioid}: {e}")
            if args['printerr']:
                print(f"GPIO init error for pin {ioid}: {e}")

def get_device_io_config(args):
    global ds18x20_devices
    header = {'Accept': 'application/json', 'Content-Type': 'application/json', 'charset': 'utf8'}
    results = {'status': False}
    for i in range(2):
        if results['status']:
            break
        if i == 1:
            args = connect(args)
        for host in apihosts:
            wdt.feed()
            apiurl = f"http://{host}:8888/homatica_devices?method=get_device_io_conf"
            try:
                gc.collect()
                print(f"Free memory before get_io_config: {gc.mem_free()}")
                start_time = utime.ticks_ms()
                response = urequests.post(apiurl, headers=header, json=args, timeout=5)
                print(f"Get IO config request took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                if response.status_code != 200:
                    if args['printerr']:
                        print(f"Error with get_device_io_config {apiurl}")
                else:
                    tmp = response.json()
                    #print(f"get_device_io_config response: {tmp}")
                    if 'status' in tmp and tmp['status'] == '200 Ok':
                        results['io_conf'] = tmp['results']['io_conf']
                        results['api_error'] = False
                        results['status'] = True
                        print(f"Received io_conf: {results['io_conf']}")
                        init_gpio_ports(args, results['io_conf'])
                        break
            except Exception as err:
                args['err'] = err
                results['api_error'] = api_err_handler(args)
                if args['printerr']:
                    print(f"Error with get_device_io_config: {err} {apiurl}")
            finally:
                if 'response' in locals():
                    response.close()
            gc.collect()
            print(f"Free memory after get_io_config: {gc.mem_free()}")
    if args['printdebug']:
        print(f"get_device_io_config: {results.get('io_conf', 'None')}")
    if not results['status']:
        log_error("get_device_io_config failed")
    return results

def read_input_vals(args, io_conf):
    global ds18x20_devices, aht20_devices
    input_vals = {}

    for raw_ioid in io_conf:
        tmp = io_conf[raw_ioid]
        if tmp['io_mode'] == 'IN' and tmp['io_type'] != 'manual':
            start_time = utime.ticks_ms()

            if tmp['io_type'] == 'ADC':
                try:
                    adc = m.ADC(int(raw_ioid))
                    tmp['io_v'] = adc.read_u16()
                    tmp['io_freq'] = ''
                    tmp['io_duty'] = ''
                    if raw_ioid == '4':
                        tmp['io_v'] = 27 - (((tmp['io_v'] * (3.3 / 65536)) - 0.706) / 0.001721)
                    input_vals[raw_ioid] = tmp
                except Exception as e:
                    log_error(f"ADC error for IO {raw_ioid}: {e}")
                    if args.get('printerr'):
                        print(f"ADC error for IO {raw_ioid}: {e}")

            elif tmp['io_type'] == 'DS18x20':
                try:
                    tmp['io_v'] = 0
                    tmp['io_freq'] = ''
                    tmp['io_duty'] = ''
                    if raw_ioid in ds18x20_devices:
                        dsd, device = ds18x20_devices[raw_ioid]
                        dsd.convert_temp()
                        utime.sleep_ms(750)
                        tmp['io_v'] = dsd.read_temp(device)
                    else:
                        log_error(f"No DS18x20 devices cached for pin {raw_ioid}")
                    input_vals[raw_ioid] = tmp
                except Exception as e:
                    log_error(f"DS18x20 read error for IO {raw_ioid}: {e}")
                    if args.get('printerr'):
                        print(f"DS18x20 read error for IO {raw_ioid}: {e}")

            elif tmp['io_type'] == 'AHT20':
                try:
                    sensor = aht20_devices.get(raw_ioid)
                    if sensor:
                        temp, hum = sensor.read_data()
                        tmp['io_v'] = {'temperature': temp, 'humidity': hum}
                        tmp['io_freq'] = ''
                        tmp['io_duty'] = ''
                        input_vals[raw_ioid] = tmp
                    else:
                        log_error(f"No AHT20 sensor found for IO {raw_ioid}")
                except Exception as e:
                    log_error(f"AHT20 read error for IO {raw_ioid}: {e}")
                    if args.get('printerr'):
                        print(f"AHT20 read error for IO {raw_ioid}: {e}")

            print(f"Read IO {raw_ioid} took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")

    if args.get('printdebug'):
        print(f"read_input_vals: {input_vals}")

    return input_vals

def apply_outputs(args, device_outputs):
    try:
        # Iterate over the 'data' list in device_outputs
        for output in device_outputs['data']:
            ioid = output['ioid']  # Get the ioid from the dictionary
            tmp = output  # Use the output dictionary directly
            if args['printdebug']:
                print(f"apply_outputs: {tmp}")
            
            # Check if PWM is configured (io_freq and io_duty are non-empty)
            if io_conf[ioid]['io_type'] == 'PWM' and tmp['io_freq'] != '' and tmp['io_duty'] != '':
                io = m.PWM(m.Pin(int(ioid)))
                dc = round(65536 * (float(tmp['io_duty']) / 100))
                io.freq(int(tmp['io_freq']))
                io.duty_u16(dc)
            elif io_conf[ioid]['io_type'] in ['switch_inv', 'switch'] :
                # Check if switch is inverted
                is_inverted = io_conf[ioid]['io_type'] == 'switch_inv'
                pin = m.Pin(int(ioid), m.Pin.OUT)
                
                # Apply on/off logic based on io_v and inversion
                if tmp['io_v'] == 'on':
                    pin.off() if is_inverted else pin.on()
                elif tmp['io_v'] == 'off':
                    pin.on() if is_inverted else pin.off()
                
    except Exception as err:
        log_error(f"Error apply_outputs: {err}")
        if args['printerr']:
            print(f"Error apply_outputs: {err}")
            
def set_time(args):
    if not args['iscon']:
        args = connect(args)
    for i in range(3):
        try:
            start_time = utime.ticks_ms()
            ntptime.settime()
            print(f"Time set took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
            break
        except Exception as err:
            log_error(f"Failed to set time: {err}")
            print(f"Failed to set time: {err}")
            utime.sleep(0.25)

def main(args):
    global client, io_conf, wlan
    wdt.feed()
    refrsh_int = 1
    start_time = utime.ticks_ms()
    set_time(args)
    print(f"set_time took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
    last_availability_publish = 0
    while True:
        start_time = utime.ticks_ms()
        gc.collect()
        print(f"Loop start: Free memory {gc.mem_free()}, Time {start_time}")
        if not args['iscon'] or not wlan.isconnected():
            args = connect(args)
            print(f"Connect took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
            start_time = utime.ticks_ms()
        if args['iscon'] and not args['isreged']:
            tmp = register(args)
            print(f"Register took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
            start_time = utime.ticks_ms()
            if tmp['api_error'] == False:
                wdt.feed()
                args = tmp['args']
                refrsh_int = args['refrsh_int']
                print(f"Registered: {args}")
                tmp = get_device_io_config(args)
                print(f"Get IO config took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                start_time = utime.ticks_ms()
                if tmp['api_error'] == False:
                    args['hasioconf'] = True
                    io_conf = tmp['io_conf']
                    print(f"Got IO Config: {args}")
        if args['hasioconf'] and args.get('mqtt_input_topic') and args.get('mqtt_output_topic'):
            wdt.feed()
            if client is None:
                mqtt_reconnect(args)
            if client is not None:
                try:
                    client.check_msg()
                    print(f"MQTT setup took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                    print("MQTT connected and subscribed to outputs")
                except OSError as e:
                    log_error(f"MQTT check_msg failed: {e}")
                    mqtt_reconnect(args)
                except Exception as e:
                    log_error(f"MQTT other error: {e}")
        led_0.on()
        args['input_vals'] = read_input_vals(args, io_conf)
        print(f"Read input vals took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
        start_time = utime.ticks_ms()
        if args['iscon'] and args['isreged'] and args.get('mqtt_input_topic') and client:
            wdt.feed()
            try:
                client.publish(args['mqtt_input_topic'], ujson.dumps({'device_grp':args['device_grp'], 'device_id': args['device_id'], 'device_name':args['device_name'], 'updated': utime.time(), 'input_vals': args['input_vals']}))
                print(f"Published inputs: {args['input_vals']}")
                print(f"Publish took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                start_time = utime.ticks_ms()
                if utime.time() - last_availability_publish > 30:
                    client.publish(f"{args['mqtt_input_topic']}/availability", "online", retain=True)
                    last_availability_publish = utime.time()
                    print(f"Availability publish took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
                    start_time = utime.ticks_ms()
                    print("Published availability: online")
                client.check_msg()
                print(f"Check msg took {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
            except OSError as e:
                log_error(f"MQTT publish/check failed: {e}")
                mqtt_reconnect(args)
            except Exception as e:
                log_error(f"MQTT publish error: {e}")
                if args['printerr']:
                    print(f"MQTT publish error: {e}")
        
        led_0.off()
        print(f"Loop end: Total time {utime.ticks_diff(utime.ticks_ms(), start_time)}ms")
        utime.sleep(refrsh_int)

if __name__ == '__main__':
    try:
        print("Script starting...")
        main(args)
    except Exception as e:
        log_error(f"Main loop error: {e}")
        print(f"Main loop error: {e}")
        utime.sleep(5)
        main(args)
