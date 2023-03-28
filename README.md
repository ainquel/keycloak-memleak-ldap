This project helps to reproduce a memory leak described [here](https://github.com/keycloak/keycloak/issues/19396)
when using multiple ldap user federations in one realm.

It uses `docker` and `docker-compose` to start a keycloak instance and a
script which:

- starts some ldap instances and register them in keycloak
- runs searches of an unknown user (to bypass the cache and query the ldap server)
- non stop ldap synchronization jobs
- monitoring of the number of instances of a few keycloak classes involved in
  the leak and of the heap memory used by keycloak (using `jcmd` and `jmap`).

The whole thing is running on the host network to help test a dev keycloak instance
built from the source code. The ports `8081`, `10389-10400` must be available.

The `scripts` container declares extra capabilities in `docker-compose.yml` to
be able to monitor the keycloak java process.

It has been tested on versions: **18.0.2**, **21.0.1**. To test another image, it is possible
to change the version in the `docker-compose.yml`

## getting started

```bash
docker-compose up --build -d
# to follow the classes/memory monitoring
docker-compose logs -f scripts
# to get kc logs
docker-compose logs -f kc
```

### Start the test with the keycloak dev server

Once the dev server is [started](https://github.com/keycloak/keycloak/blob/main/docs/tests.md#keycloak-server),
run `docker-compose up --build scripts`.

## config

it is possible to change the number of search threads or the number of ldap instances
by modifying service `scripts` env variables in the compose file.

```yaml
environment:
  SEARCH_COUNT: 8
  LDAP_COUNT: 4
```

## results observed

On a 8 i7 CPU core workstation with 16 search threads and 8 ldap instances:

When using the fix described on the issue above, keycloak consumes
`150m to 300m` of heap constantly (tested in a period of 30 minutes).

Without the fix it reaches `1G` of heap in 3 minutes.

## notes on monitoring

The script uses `jmap -histo:live PID` to monitor classes. It forces a full gc before reporting
stats.  
The memory stats comes from the field `used` in the output of `jcmd PID GC.heap_info`.

## notes on ldap instances

Ldap instances are cleaned up when the program receives a `SIGTERM`, so it's normal
if it takes a few seconds to stop the container `scripts`.
