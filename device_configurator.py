import json
import os
import yaml
from datetime import datetime
from netmiko import ConnectHandler, NetmikoTimeoutException, NetmikoAuthenticationException

# Cargar configuraciones en YAML
def cargar_yaml(ruta):
    try:
        with open(ruta, "r", encoding="utf-8") as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        print(f"Error: No se encontró el archivo en {ruta}")
        return None

# Config a comandos cisco
def traducir_configuracion_a_comandos(config):
    comandos = []

    # Hostname
    if "hostname" in config:
        comandos.append(f"hostname {config['hostname']}")

    # Loopbacks
    if "loopbacks" in config:
        for lb in config["loopbacks"]:
            comandos.append(f"interface {lb['nombre']}")
            comandos.append(f"description {lb['descripcion']}")
            comandos.append(f"ip address {lb['ip']} {lb['mascara']}")
            comandos.append("no shutdown")
            comandos.append("exit")

    # Interfaces Fisicas
    if "interfaces_fisicas" in config:
        for intf in config["interfaces_fisicas"]:
            comandos.append(f"interface {intf['interfaz']}")
            comandos.append(f"description {intf['descripcion']}")
            comandos.append("exit")

    # ACLs
    if "listas_acceso" in config:
        for acl in config["listas_acceso"]:
            numero = acl["numero"]
            for regla in acl["reglas"]:
                comandos.append(f"access-list {numero} {regla}")

    # Rutas Estaticas
    if "rutas_estaticas" in config:
        for ruta in config["rutas_estaticas"]:
            comandos.append(f"ip route {ruta['red']} {ruta['mascara']} {ruta['siguiente_salto']}")

    # OSPF
    if "ospf" in config:
        ospf = config["ospf"]
        comandos.append(f"router ospf {ospf['proceso']}")
        if "router_id" in ospf:
            comandos.append(f"router-id {ospf['router_id']}")
        for red in ospf["redes"]:
            comandos.append(f"network {red['red']} {red['wildcard']} area {red['area']}")
        comandos.append("exit")

    return comandos

# Validar config inyectada
def verificar_cambios(conexion):
    verificaciones = {}

    # Interfaces
    salida = conexion.send_command("show ip interface brief")
    interfaces = []
    for linea in salida.splitlines()[1:]:
        partes = linea.split()
        if len(partes) >= 6:
            interfaces.append({
                "interfaz":  partes[0],
                "ip":        partes[1],
                "estado":    partes[4],
                "protocolo": partes[5]
            })
    verificaciones["interfaces"] = interfaces

    # Rutas
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
        red = next((p for p in partes if "." in p and p[0].isdigit()), "N/A")
        idx = partes.index(red) if red != "N/A" else 1
        detalle = " ".join(partes[idx + 1:])
        rutas.append({
            "tipo":    tipo_map[partes[0]],
            "red":     red,
            "detalle": detalle
        })
    verificaciones["rutas"] = rutas

    # OSPF
    salida = conexion.send_command("show ip ospf interface brief")
    ospf = []
    for linea in salida.splitlines()[1:]:
        partes = linea.split()
        if len(partes) >= 7:
            ospf.append({
                "interfaz": partes[0],
                "pid":      partes[1],
                "area":     partes[2],
                "ip":       partes[3],
                "estado":   partes[5]
            })
    verificaciones["ospf"] = ospf

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
    verificaciones["acls"] = acls

    return verificaciones

# Guardar resultados en un archivo JSON
def generar_reporte(datos, directorio="reports"):
    os.makedirs(directorio, exist_ok=True)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    nombre_archivo = f"{directorio}/validacion_TovARP_{timestamp}.json"

    with open(nombre_archivo, "w", encoding="utf-8") as f:
        json.dump(datos, f, indent=4, ensure_ascii=False)

    return nombre_archivo

def main():
    print("-- Configurador de Comandos --")

    # Cargar credenciales y config
    credenciales = cargar_yaml("config/device.yaml")
    config_red = cargar_yaml("config/configuraciones.yaml")

    if not credenciales or not config_red:
        return

    device_config = credenciales["device"]
    comandos_ios = traducir_configuracion_a_comandos(config_red)

    try:
        conexion = ConnectHandler(**device_config)
        print("Conexión lista")

        # Aplicar configs
        salida_config = conexion.send_config_set(comandos_ios)
        print("Configuraciones enviadas al dispositivo")

        # Verificar
        resultados_verificacion = verificar_cambios(conexion)

        # Cerrar conexion
        conexion.disconnect()
        print("\nConexión finalizada")

        # Estructura de datos para el reporte
        datos = {
            "dispositivo": device_config["host"],
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "comandos_aplicados": comandos_ios,
            "log_configuracion": salida_config,
            "estado_final": resultados_verificacion
        }

        generar_reporte(datos)

    except NetmikoAuthenticationException:
        print("\nError: Fallo de autenticacion. Verifique sus credenciales de DevNet")
    except NetmikoTimeoutException:
        print("\nError: Tiempo de espera agotado. El Sandbox podría estar apagado o bloqueado.")
    except Exception as e:
        print(f"\nError inesperado: {e}")

if __name__ == "__main__":
    main()