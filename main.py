import functools
import os
import re
import signal
import subprocess
import time
import traceback
import warnings
from concurrent.futures import ThreadPoolExecutor

import docker as dockerlib
import keycloak
import requests
from keycloak import KeycloakAdmin

warnings.filterwarnings("ignore")

dc = "dc=keycloak,dc=org"
users_dn = f"ou=users,{dc}"
ldap_config = {
    "pagination": "true",
    "usersDn": users_dn,
    "connectionPooling": "true",
    "cachePolicy": "NONE",
    "importEnabled": "true",
    "enabled": "true",
    "usernameLDAPAttribute": "uid",
    "bindDn": f"cn=admin,{dc}",
    "bindCredential": "password",
    "uuidLDAPAttribute": "uid",
    "authType": "simple",
    "userObjectClasses": "inetOrgPerson",
    "editMode": "READ_ONLY",
    "batchSizeForSync": "1000"
}
docker = dockerlib.from_env()
docker_label = "kc-reproducer"
url = "http://localhost:8081/auth/"

print = functools.partial(print, flush=True)


def wait_for_kc():
    print("waiting for kc being up")
    s = requests.Session()
    while 1:
        try:
            r = s.get(url, timeout=2)
            if r.status_code == 200:
                break
        except requests.exceptions.ConnectionError as e:
            print("can't join kc for now", e)
            ...
        time.sleep(2)


def _ldaps(client):
    return [c for c in client.get_components() if c["providerId"] == "ldap"]


def run(client, func):
    while 1:
        try:
            func()
        except (
            keycloak.exceptions.KeycloakGetError,
            keycloak.exceptions.KeycloakAuthenticationError
        ) as e:
            if re.search(r"50\d:", str(e)):
                time.sleep(5)
                continue
            elif "401" in str(e):
                client.refresh_token()
                continue
            elif "Can't connect to server" in str(e):
                time.sleep(1)
                continue
            print(traceback.format_exc())
        except KeyboardInterrupt:
            return
        except Exception:
            print(traceback.format_exc())


def search_unknown(client):
    def _inner():
        client.get_users(query={"max": 1, "username": "unknown"})
    
    run(client, _inner)


def sync_ldaps(client):
    def _inner():
        try:
            for ldap in _ldaps(client):
                client.sync_users(ldap["id"], "triggerChangedUsersSync")
        except Exception as e:
            if str(e).startswith("500:"):
                return
            raise e
    
    run(client, _inner)


def clean_docker():
    for c in ldap_containers():
        c.remove(force=True, v=True)


def ldap_containers():
    return docker.containers.list(all=True, filters={"label": docker_label})


def cleanup_providers(client):
    for ldap in _ldaps(client):
        client.delete_component(ldap["id"])


def create_ldaps(client):
    count = int(os.environ.get("LDAP_COUNT", 12))
    realm = client.get_realm(client.realm_name)

    clean_docker()
    cleanup_providers(client)

    for i in range(count):
        port = 10389 + i
        name = f"ldap-{port}"

        print("creating ldap", name)
        docker.containers.run(
            "bitnami/openldap:2.6.3",
            name=name,
            detach=True,
            ports={1389: port},
            environment={
                "LDAP_ROOT": dc,
                "LDAP_ADMIN_USERNAME": "admin",
                "LDAP_ADMIN_PASSWORD": "password",
                "LDAP_USERS": f"user{i}",
                "LDAP_PASSWORDS": f"user{i}",
                "LDAP_USER_DC": "users",
                "LDAP_GROUP": "test"
            },
            labels=[docker_label]
        )

        provider_config = {
            "name": name,
            "providerId": "ldap",
            "providerType": "org.keycloak.storage.UserStorageProvider",
            "parentId": realm["id"],
            "config": {
                **{k: [v] for k, v in ldap_config.items()},
                "connectionUrl": [f"ldap://localhost:{port}"]
            }
        }
        client.create_component(provider_config)


def monitor_kc():
    procrun = functools.partial(
        subprocess.run,
        shell=True,
        check=True,
        capture_output=True,
        text=True
    )
    # match either quarkus (production mode) or the dev server 
    r = procrun("jcmd | grep -P '(io.quarkus.bootstrap.runner.QuarkusEntryPoint|Pkeycloak-server)'")
    lines = r.stdout.splitlines()
    if len(lines) != 1:
        raise ValueError("couldn't get keycloak process or multiple keycloak instances are running")
    
    pid = lines[0].split()[0].strip()

    classes = '|'.join([
        "DefaultKeycloakSession",
        "QuarkusKeycloakSession",
        "LDAPIdentityStore",
        "LDAPStorageProvider",
    ])

    while 1:
        r = procrun(f'jmap -histo:live {pid} | grep -P "({classes})$"')
        print("number of instances:")
        for line in r.stdout.splitlines():
            s = line.strip().split()
            spaces = " " * (10 - len(s[1]))
            print(f"{s[1]}{spaces}{s[-1]}")

        r = procrun(f"jcmd {pid} GC.heap_info | grep used")
        m = re.search(r"used (\d+)K ", r.stdout)
        if not m:
            print("heap info not found using jcmd")
            time.sleep(5)
        mem_mega = int(m.group(1)) / 1000
        print(f"mem used: {mem_mega:.2f}m\n")
        time.sleep(5)


_executor = None

def main():
    global _executor

    wait_for_kc()
    client = KeycloakAdmin(
        server_url=url,
        username="admin",
        password="admin",
        realm_name="master",
        verify=False,
        timeout=10
    )
    create_ldaps(client)

    search_count = int(os.environ.get("SEARCH_COUNT", 8))
    _executor = ThreadPoolExecutor(max_workers=100)
    print(f"starting {search_count} search threads")
    for _ in range(search_count):
        _executor.submit(search_unknown, client)
    print("starting synchronization every second")
    _executor.submit(sync_ldaps, client)
    print("starting java monitoring")
    monitor_kc()


if __name__ == "__main__":
    print("starting reproducer")

    def shutdown(*args):
        clean_docker()
        if _executor:
            _executor.shutdown(wait=True, cancel_futures=True)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        main()
    except Exception:
        print(traceback.format_exc())
    finally:
        shutdown()
