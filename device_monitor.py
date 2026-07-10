import json
import os
import time
import yaml
from datetime import datetime
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

# credenciales del dispositivo y config del monitor
def cargar_configuracion(ruta="config/device.yaml"):
    with open(ruta, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    return config["device"], config["monitor"]

# conexion ssh
def conectar(device_config):
    conexion = ConnectHandler(**device_config)
    return conexion

# estado actual del dispositivo
def obtener_estado(conexion):
    estado = {}

    # interfaces
    salida = conexion.send_command("show ip interface brief")
    interfaces = []
    for linea in salida.splitlines()[1:]:
        partes = linea.split()
        if len(partes) >= 6:
            interfaces.append({
                "interfaz":  partes[0],
                "ip": partes[1],
                "estado": partes[4],
                "protocolo": partes[5]
            })
    estado["interfaces"] = interfaces

    # rutas
    salida = conexion.send_command("show ip route")
    rutas = []
    tipo_map = {
        "C": "Conectada", "S": "Estática", "O": "OSPF",
        "R": "RIP", "B": "BGP", "D": "EIGRP",
        "E": "EIGRP externo", "I": "IGRP"
    }
    for linea in salida.splitlines():
        partes = linea.split()
        if not partes or partes[0] not in tipo_map:
            continue
        red = next((p for p in partes if "." in p and p[0].isdigit()), None)
        if red is None:
            continue
        idx = partes.index(red)
        detalle = " ".join(partes[idx + 1:])
        rutas.append({
            "tipo": tipo_map[partes[0]],
            "red": red,
            "detalle": detalle
        })
    estado["rutas"] = rutas

    # OSPF
    salida = conexion.send_command("show ip ospf interface brief")
    ospf = []
    for linea in salida.splitlines()[1:]:
        partes = linea.split()
        if len(partes) >= 7:
            ospf.append({
                "interfaz": partes[0],
                "pid": partes[1],
                "area": partes[2],
                "ip": partes[3],
                "estado": partes[5]
            })
    estado["ospf"] = ospf

    # ACLs
    salida = conexion.send_command("show access-lists")
    acls = []
    acl_actual = None
    for linea in salida.splitlines():
        if "access list" in linea.lower():
            acl_actual = {"nombre": linea.strip(), "reglas": []}
            acls.append(acl_actual)
        elif acl_actual and linea.strip() and "#" not in linea:
            acl_actual["reglas"].append(linea.strip())
    estado["acls"] = acls

    return estado

# comparad dos snapshots y retornad una lista de cambios detectados
def comparar_estados(anterior, actual):
    cambios = []

    # Interfaces
    anterior_ifaces = {i["interfaz"]: i for i in anterior.get("interfaces", [])}
    actual_ifaces = {i["interfaz"]: i for i in actual.get("interfaces", [])}

    for nombre, datos in actual_ifaces.items():
        if nombre not in anterior_ifaces:
            # interfaz que existe ahora pero no existia antes
            cambios.append(f"[INTERFAZ NUEVA] {nombre} — IP: {datos['ip']} Estado: {datos['estado']}/{datos['protocolo']}")
        else:
            ant = anterior_ifaces[nombre]
            # cambio de estado de linea o protocolo
            if datos["estado"] != ant["estado"] or datos["protocolo"] != ant["protocolo"]:
                cambios.append(f"[INTERFAZ CAMBIO] {nombre} — {ant['estado']}/{ant['protocolo']} → {datos['estado']}/{datos['protocolo']}")
            # cambio de IP en la interfaz
            if datos["ip"] != ant["ip"]:
                cambios.append(f"[INTERFAZ IP] {nombre} — {ant['ip']} → {datos['ip']}")

    for nombre in anterior_ifaces:
        if nombre not in actual_ifaces:
            # interfaz que existia antes pero ahora ya no
            cambios.append(f"[INTERFAZ ELIMINADA] {nombre}")

    # Rutas
    anterior_rutas = {r["red"]: r for r in anterior.get("rutas", [])}
    actual_rutas   = {r["red"]: r for r in actual.get("rutas", [])}

    for red, datos in actual_rutas.items():
        if red not in anterior_rutas:
            cambios.append(f"[RUTA NUEVA] {datos['tipo']} — {red} {datos['detalle']}")
        else:
            ant = anterior_rutas[red]
            if datos["tipo"] != ant["tipo"]:
                cambios.append(f"[RUTA CAMBIO] {red} — tipo {ant['tipo']} → {datos['tipo']}")

    for red in anterior_rutas:
        if red not in actual_rutas:
            cambios.append(f"[RUTA ELIMINADA] {anterior_rutas[red]['tipo']} — {red}")

    # OSPF
    anterior_ospf = {o["interfaz"]: o for o in anterior.get("ospf", [])}
    actual_ospf   = {o["interfaz"]: o for o in actual.get("ospf", [])}

    for iface, datos in actual_ospf.items():
        if iface not in anterior_ospf:
            cambios.append(f"[OSPF NUEVA INTERFAZ] {iface} — Area: {datos['area']} Estado: {datos['estado']}")
        else:
            ant = anterior_ospf[iface]
            # cambio en el estado de adyacencia OSPF
            if datos["estado"] != ant["estado"]:
                cambios.append(f"[OSPF CAMBIO] {iface} — {ant['estado']} → {datos['estado']}")

    for iface in anterior_ospf:
        if iface not in actual_ospf:
            cambios.append(f"[OSPF INTERFAZ ELIMINADA] {iface}")

    # ACLs
    anterior_acls = {a["nombre"]: a for a in anterior.get("acls", [])}
    actual_acls   = {a["nombre"]: a for a in actual.get("acls", [])}

    for nombre, datos in actual_acls.items():
        if nombre not in anterior_acls:
            cambios.append(f"[ACL NUEVA] {nombre}")
        else:
            ant = anterior_acls[nombre]
            # si se difiere en cualquier elemento es considerado cambio
            if datos["reglas"] != ant["reglas"]:
                cambios.append(f"[ACL MODIFICADA] {nombre}")

    for nombre in anterior_acls:
        if nombre not in actual_acls:
            cambios.append(f"[ACL ELIMINADA] {nombre}")

    return cambios

# agregar un nuevo evento con sus cambios y timestamp
def registrar_evento(log, cambios, timestamp):
    log.append({
        "timestamp": timestamp,
        "cambios":   cambios
    })

# guardar el log en un archivo JSON
def guardar_log(log, archivo):
    with open(archivo, "w", encoding="utf-8") as f:
        json.dump(log, f, indent=4, ensure_ascii=False)

# imprimr en consola los cambios
def imprimir_alerta(cambios, timestamp):
    print(f"\n[{timestamp}] CAMBIOS DETECTADOS:")
    for cambio in cambios:
        print(f"  → {cambio}")

def main():
    device_config, monitor_config = cargar_configuracion()
    intervalo = monitor_config["intervalo_segundos"]

    os.makedirs("reports", exist_ok=True)
    timestamp_sesion = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    archivo_log = f"reports/monitor_log_{timestamp_sesion}.json"
    log = []

    print(f"TovARP Monitor — {device_config['host']}")
    print(f"Intervalo: {intervalo}s | Log: {archivo_log}")

    try:
        conexion = ConnectHandler(**device_config)
        print("Conexion lista")

        # primer snapshot
        estado_anterior = obtener_estado(conexion)
        print("Snapshot inicial listo\n")

        while True:
            time.sleep(intervalo)
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            estado_actual = obtener_estado(conexion)
            cambios = comparar_estados(estado_anterior, estado_actual)

            if cambios:
                imprimir_alerta(cambios, timestamp)
                registrar_evento(log, cambios, timestamp)
                guardar_log(log, archivo_log)
            else:
                print(f"[{timestamp}] Sin cambios")

            # el estado actual pasa a ser el anterior para el siguiente ciclo
            estado_anterior = estado_actual

    except KeyboardInterrupt:
        print("\n\nMonitoreo detenido por el usuario")
        print(f"Log guardado en: {archivo_log}")

    except NetmikoAuthenticationException:
        print("\nError: Fallo de autenticacion. Verifique sus credenciales de DevNet")

    except NetmikoTimeoutException:
        print("\nError: Tiempo de espera agotado. El Sandbox podria estar apagado.")

    except Exception as e:
        print(f"\nError inesperado: {e}")

    finally:
        try:
            conexion.disconnect()
        except:
            pass

if __name__ == "__main__":
    main()