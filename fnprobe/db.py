import logging
from enum import Enum
import psycopg2

# Current mapping between probe and error types. Used for storage (probe) and
# analysis. (analyze)
probeTypes = Enum('BANDWIDTH', 'BUILD', 'IDENTIFIER', 'LINK_LENGTHS',
                  'LOCATION', 'STORE_SIZE', 'UPTIME_48H',
                  'UPTIME_7D', 'REJECT_STATS')
errorTypes = Enum('DISCONNECTED', 'OVERLOAD', 'TIMEOUT', 'UNKNOWN',
                  'UNRECOGNIZED_TYPE', 'CANNOT_FORWARD')


class Database:
    """Handles database connection, initialization, and analysis queries."""

    def __init__(self, config):
        """
        Initialize the database if it does not already exist. If it already
        exists and is not the latest version, upgrade it.

        Exposes maintenance, record addition, and reading connections as
        maintenance, add, and read respectively.

        :type config: dict contains at least database, maintenance_user,
        read_user, add_user. maintenance_pass, read_pass, and add_pass are also
        recognized. Other parameters are passed to the database as keyword
        arguments.
        """

        auth = {}
        # Move manually used parameters into expected config so they are not
        # specified again as additional keyword arguments. Passwords need not
        # be specified as there are methods of authentication that do not use
        # them.
        for parameter in ['maintenance_user', 'maintenance_pass',
                          'read_user', 'read_pass', 'add_user', 'add_pass']:
            auth[parameter] = config.get(parameter)
            if parameter in config:
                del config[parameter]

        self.maintenance = psycopg2.connect(user=auth['maintenance_user'],
                                            password=auth['maintenance_pass'],
                                            **config)

        cur = self.maintenance.cursor()
        try:
            cur.execute("""
            SELECT
              schema_version
            FROM
              meta""")
            version = cur.fetchone()[0]
            self.maintenance.commit()

            # The database has already been set up. Upgrade to the latest
            # version if necessary.
            logging.info("Found version {0}.".format(version))
            self.upgrade(version, config)
        except psycopg2.ProgrammingError, e:
            logging.debug("Got '{0}' when querying version.".format(e.pgerror))
            # If there are no tables in this database, it is new, so set up the
            # latest version.
            self.maintenance.commit()
            self.create_new()

            # Grant permissions to the newly created tables.
            # TODO: This list is brittle if new tables are added as it must
            # also be updated. Would it make sense to instead assume that
            # only pyProbe stuff is in the database and grant permissions to
            # all tables? (select table_name from {database}.tables...)
            tables = """
                      "bandwidth", "build", "identifier", "peer_count",
                      "link_lengths", "location", "store_size", "reject_stats",
                      "uptime_48h", "uptime_7d", "error", "refused"
                     """

            cur.execute("""
            GRANT
              INSERT
            ON TABLE
              {0}
            TO
              "{1}"
            """.format(tables, auth['add_user']))
            cur.execute("""
            GRANT
              SELECT
            ON
              {0}
            TO
              "{1}"
            """.format(tables, auth['read_user']))

            self.maintenance.commit()

        self.read = psycopg2.connect(user=auth['read_user'],
                                     password=auth['read_pass'], **config)
        self.add = psycopg2.connect(user=auth['add_user'],
                                    password=auth['add_pass'], **config)

    def create_new(self):
        logging.warning("Setting up new tables.")

        cur = self.maintenance.cursor()

        cur.execute("""
        CREATE TABLE
          bandwidth(
                    id       SERIAL PRIMARY KEY,
                    time     TIMESTAMP WITH TIME ZONE,
                    duration INTERVAL,
                    htl      INTEGER,
                    KiB      FLOAT
                   )""")

        cur.execute("""
        CREATE TABLE
          build(
                id       SERIAL PRIMARY KEY,
                time     TIMESTAMP WITH TIME ZONE,
                duration INTERVAL,
                htl      INTEGER,
                build    INTEGER
               )""")

        cur.execute("""
        CREATE TABLE
          identifier(
                     id         SERIAL PRIMARY KEY,
                     time       TIMESTAMP WITH TIME ZONE,
                     duration   INTERVAL,
                     htl        INTEGER,
                     identifier BIGINT,
                     percent    INTEGER
                    )""")

        # peer_count is out of alphabetical order here, but it must exist before
        # link_lengths because link_lengths REFERENCES this table.
        # TODO: Does that actually matter? Is there a nicer way to format all
        # these statements?
        cur.execute("""
        CREATE TABLE
          peer_count(
                     id       SERIAL PRIMARY KEY,
                     time     TIMESTAMP WITH TIME ZONE,
                     duration INTERVAL,
                     htl      INTEGER,
                     peers    INTEGER
                    )""")

        cur.execute("""
        CREATE TABLE
          link_lengths(
                       id       SERIAL PRIMARY KEY,
                       length   FLOAT,
                       count_id INTEGER REFERENCES peer_count
                      )""")

        cur.execute("""
        CREATE TABLE
          location(
                   id       SERIAL PRIMARY KEY,
                   time     TIMESTAMP WITH TIME ZONE,
                   duration INTERVAL,
                   htl      INTEGER,
                   location FLOAT
                  )""")

        cur.execute("""
        CREATE TABLE
          store_size(
                     id       SERIAL PRIMARY KEY,
                     time     TIMESTAMP WITH TIME ZONE,
                     duration INTERVAL,
                     htl      INTEGER,
                     GiB      FLOAT
                    )""")

        cur.execute("""
        CREATE TABLE
          reject_stats(
                       id               SERIAL PRIMARY KEY,
                       time             TIMESTAMP WITH TIME ZONE,
                       duration         INTERVAL,
                       htl              INTEGER,
                       bulk_request_chk INTEGER,
                       bulk_request_ssk INTEGER,
                       bulk_insert_chk  INTEGER,
                       bulk_insert_ssk  INTEGER
                      )""")

        cur.execute("""CREATE TABLE
          uptime_48h(
                     id       SERIAL PRIMARY KEY,
                     time     TIMESTAMP WITH TIME ZONE,
                     duration INTERVAL,
                     htl      INTEGER,
                     percent  FLOAT
                    )""")

        cur.execute("""
        CREATE TABLE
          uptime_7d(
                    id       SERIAL PRIMARY KEY,
                    time     TIMESTAMP WITH TIME ZONE,
                    duration INTERVAL,
                    htl      INTEGER,
                    percent  FLOAT
                   )""")

        cur.execute("""
        CREATE TABLE
          error(
                id         SERIAL PRIMARY KEY,
                time       TIMESTAMP WITH TIME ZONE,
                duration   INTERVAL,
                htl        INTEGER,
                local      BOOLEAN,
                probe_type INTEGER,
                error_type INTEGER,
                code       INTEGER
               )""")

        cur.execute("""CREATE TABLE
          refused(
                  id         SERIAL PRIMARY KEY,
                  time       TIMESTAMP WITH TIME ZONE,
                  duration   INTERVAL,
                  htl        INTEGER,
                  probe_type INTEGER
                 )""")

        cur.execute("""
        CREATE TABLE
          meta(
               schema_version INTEGER
              )""")
        cur.execute("""
        INSERT INTO
          meta(schema_version)
          values(0)""")

        self.maintenance.commit()
        self.create_indexes()
        logging.warning("Table setup complete.")

    def create_indexes(self):
        cur = self.maintenance.cursor()

        cur.execute("""
        CREATE INDEX
          bandwidth_time_index
        ON
          bandwidth(time)""")
        cur.execute("""
        CREATE INDEX
          build_time_index
        ON
          build(time)""")
        cur.execute("""
        CREATE INDEX
          identifier_identifier_time
        ON
          identifier(identifier, time)""")
        cur.execute("""
        CREATE INDEX
          identifier_time_identifier
        ON
          identifier(time, identifier)""")
        cur.execute("""
        CREATE INDEX
          peer_count_time_index
        ON
          peer_count(time)
        """)
        cur.execute("""
        CREATE INDEX
          location_time_index
        ON
          location(time)""")
        cur.execute("""
        CREATE INDEX
          store_size_time_index
        ON
          store_size(time)""")
        cur.execute("""
        CREATE INDEX
          reject_stats_time_index
        ON
          reject_stats(time)""")
        cur.execute("""
        CREATE INDEX
          uptime_48h_time_index
        ON
          uptime_48h(time)""")
        cur.execute("""
        CREATE INDEX
          uptime_7d_time_index
        ON
          uptime_7d(time)""")
        cur.execute("""
        CREATE INDEX
          error_time_index
        ON
          error(time)""")
        cur.execute("""
        CREATE INDEX
          refused_time_index
        ON
          refused(time)""")

        self.maintenance.commit()

    def drop_indexes(self):
        cur = self.maintenance.cursor()

        for index in ['bandwidth_time_index', 'build_time_index',
                      'identifier_identifier_time',
                      'identifier_time_identifier', 'peer_count_time_index',
                      'location_time_index', 'store_size_time_index',
                      'reject_stats_time_index', 'uptime_48h_time_index',
                      'uptime_7d_time_index', 'error_time_index',
                      'refused_time_index']:
            cur.execute("""DROP INDEX {0}""".format(index))

        self.maintenance.commit()

    def upgrade(self, version, config):
        # The user names (in config) will be needed to modify permissions as
        # part of upgrades.
        pass

    def intersect_identifier(self, earliest, mid, latest):
        """
        Return a tuple of the number of distinct identifiers appearing in
        between both earliest to mid and mid to latest time spans, followed
        by the overall number of identifiers in the same conditions.
        """
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              COUNT(DISTINCT identifier), COUNT(identifier)
            FROM
              (SELECT
                i1.identifier
               FROM identifier i1
                 JOIN identifier i2
                 USING(identifier)
               WHERE i1.time BETWEEN %(earliest)s AND %(mid)s
                 AND i2.time BETWEEN %(mid) AND %(latest)s
              )
            """, {'earliest': earliest, 'mid': mid,
                  'latest': latest})
        return cur.fetchone()

    def span_identifier(self, start, end):
        """
        Return a tuple of the number of distinct identifiers and the number
        of identifiers outright in the given time span.
        """
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              COUNT(DISTINCT "identifier"), COUNT("identifier")
            FROM
              "identifier"
            WHERE
              time BETWEEN %s AND %s
            """, (start, end))
        return cur.fetchone()

    def span_store_size(self, start, end):
        """
        Return a tuple of the sum of reported store sizes and the number of
        reports in the given time span.
        """
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              sum("GiB"), count("GiB")
            FROM
              "store_size"
            WHERE
              "time" BETWEEN %s AND %s
            """, (start, end))
        return cur.fetchone()

    def span_refused(self, start, end):
        """Return the number of refused probes in the given time span."""
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              count(*)
            FROM
              "refused"
            WHERE
              "time" BETWEEN %s AND %s
            """, (start, end))
        return cur.fetchone()[0]

    def span_error_count(self, errorType, start, end):
        """Return the number of errors of the given type in the time span."""
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              count(*)
            FROM
              "error"
            WHERE
              "error_type" == %(errorType)% AND
              "time" BETWEEN %(start)s AND %(end)s
            """, {'errorType': errorType, 'start': start,
                  'end': end})
        return cur.fetchone()[0]

    def span_locations(self, start, end):
        """Return the distinct locations seen over the given time span."""
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              DISTINCT "location"
            FROM
              "location"
            WHERE
              "time" BETWEEN %s AND %s
            """, (start, end))
        return cur.fetchall()

    def span_peer_count(self, start, end):
        """Return binned peer counts over the time span."""
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              peers, count("peers")
            FROM
              "peer_count"
            WHERE
              "time" BETWEEN %s AND %s
              GROUP BY "peers"
              ORDER BY "peers"
            """, (start, end))
        return cur.fetchall()

    def span_links(self, start, end):
        """Return the list lengths seen over the time span."""
        cur = self.read.cursor()
        cur.execute("""
        SELECT
          "length"
        FROM
          "link_lengths"
        WHERE
          "time" BETWEEN %s AND %s
        """, (start, end))
        return cur.fetchall()

    def span_uptimes(self, start, end):
        """Return binned uptimes reported with identifier over the time span."""
        cur = self.read.cursor()
        cur.execute("""
            SELECT
              "percent", count("percent")
            FROM
              "identifier"
            WHERE
              "time" BETWEEN %s AND %s
            GROUP BY "percent"
            ORDER BY "percent"
            """, (start, end))
        return cur.fetchall()

    def span_bulk_rejects(self, queue_type, start, end):
        """Return binned bulk rejection percentages for the given queue type."""
        cur = self.read.cursor()
        # Report of -1 means no data.
        # Note that queue_type could cause injection because of the string
        # formatting operations, but it should be used with elements from a
        # fixed list, and the read connection should have only SELECT
        # privileges.
        cur.execute("""
            SELECT
              {0}, count({0})
            FROM
              "reject_stats"
            WHERE
              "time" BETWEEN %s AND %s
              AND {0} IS NOT -1
            GROUP BY {0}
            ORDER BY {0}
            """.format(queue_type), (start, end))
        return cur.fetchall()
