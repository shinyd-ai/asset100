import psycopg2
from psycopg2.extras import RealDictCursor
import os, re
from dotenv import load_dotenv

load_dotenv()

# Supabase 연결 정보 (Connection Pooler - Port 6543)
DB_HOST = 'aws-1-ap-northeast-2.pooler.supabase.com'
DB_NAME = 'postgres'
DB_USER = 'postgres.tswljzpcjpcevefjjgnw'
DB_PORT = '6543'
DB_PASS = os.getenv('SUPABASE_DB_PASSWORD')

class SQLiteCompatibleConnection:
    def __init__(self, conn):
        self.conn = conn

    def execute(self, sql, params=None):
        # 1. ? 를 %s 로 변경 (SQLite -> PostgreSQL)
        sql = sql.replace('?', '%s')
        
        # 2. strftime('%Y', date) -> to_char(date::date, 'YYYY') 로 변경
        # 패턴: strftime('%Y', column)
        sql = re.sub(r"strftime\('%Y-%m',\s*([^)]+)\)", r"to_char(\1::date, 'YYYY-MM')", sql)
        sql = re.sub(r"strftime\('%Y',\s*([^)]+)\)", r"to_char(\1::date, 'YYYY')", sql)
        # 패턴: strftime('%m', column)
        sql = re.sub(r"strftime\('%m',\s*([^)]+)\)", r"to_char(\1::date, 'MM')", sql)
        # 패턴: strftime('%d', column)
        sql = re.sub(r"strftime\('%d',\s*([^)]+)\)", r"to_char(\1::date, 'DD')", sql)
        
        # 3. IFNULL -> COALESCE
        sql = sql.replace('IFNULL(', 'COALESCE(')
        
        cur = self.conn.cursor(cursor_factory=RealDictCursor)
        try:
            if params:
                cur.execute(sql, params)
            else:
                cur.execute(sql)
            return cur
        except Exception as e:
            print(f"SQL Error: {sql}")
            print(f"Params: {params}")
            raise e

    def commit(self):
        self.conn.commit()

    def rollback(self):
        self.conn.rollback()

    def close(self):
        self.conn.close()

    def cursor(self):
        return self.conn.cursor(cursor_factory=RealDictCursor)

def get_db():
    conn = psycopg2.connect(
        host=DB_HOST,
        database=DB_NAME,
        user=DB_USER,
        password=DB_PASS,
        port=DB_PORT
    )
    return SQLiteCompatibleConnection(conn)

def init_db():
    conn = get_db()
    c = conn.cursor()

    # PostgreSQL 문법에 맞춰 스키마 작성 (AUTOINCREMENT -> SERIAL)
    c.execute("""
        CREATE TABLE IF NOT EXISTS income (
            id              SERIAL PRIMARY KEY,
            date            TEXT NOT NULL,
            category        TEXT,
            name            TEXT,
            memo            TEXT,
            amount          INTEGER NOT NULL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS budget (
            id              SERIAL PRIMARY KEY,
            date            TEXT NOT NULL,
            category        TEXT,
            name            TEXT,
            type            TEXT,
            payment_method  TEXT,
            amount          INTEGER NOT NULL DEFAULT 0,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS card_info (
            id              SERIAL PRIMARY KEY,
            card_num        BIGINT NOT NULL,
            card_name       TEXT,
            limit_amount    INTEGER DEFAULT 0,
            payment_day     INTEGER,
            billing_day     INTEGER,
            benefit         TEXT
        );

        CREATE TABLE IF NOT EXISTS card_tx (
            id              SERIAL PRIMARY KEY,
            card_id         INTEGER REFERENCES card_info(id),
            date            TEXT NOT NULL,
            name            TEXT,
            category        TEXT,
            amount          INTEGER NOT NULL DEFAULT 0,
            installment     INTEGER DEFAULT 1,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS stocks (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            ticker          TEXT,
            buy_date        TEXT,
            buy_price       INTEGER DEFAULT 0,
            quantity        REAL DEFAULT 0,
            current_price   INTEGER DEFAULT 0,
            dividend        INTEGER DEFAULT 0,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS stock_tx (
            id          SERIAL PRIMARY KEY,
            stock_id    INTEGER NOT NULL REFERENCES stocks(id),
            tx_date     TEXT    NOT NULL,
            tx_type     TEXT    NOT NULL,
            price       INTEGER NOT NULL DEFAULT 0,
            quantity    REAL    NOT NULL DEFAULT 0,
            fee         INTEGER DEFAULT 0,
            memo        TEXT
        );

        CREATE TABLE IF NOT EXISTS etf (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            ticker          TEXT,
            buy_date        TEXT,
            buy_price       INTEGER DEFAULT 0,
            quantity        REAL DEFAULT 0,
            current_price   INTEGER DEFAULT 0,
            etf_type        TEXT,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS crypto (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            symbol          TEXT,
            exchange        TEXT,
            buy_date        TEXT,
            buy_price       REAL DEFAULT 0,
            quantity        REAL DEFAULT 0,
            current_price   REAL DEFAULT 0,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS residence (
            id              SERIAL PRIMARY KEY,
            address         TEXT,
            deposit         INTEGER DEFAULT 0,
            monthly_rent    INTEGER DEFAULT 0,
            maintenance     INTEGER DEFAULT 0,
            start_date      TEXT,
            end_date        TEXT
        );

        CREATE TABLE IF NOT EXISTS real_estate (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            re_type         TEXT,
            purchase_date   TEXT,
            purchase_price  INTEGER DEFAULT 0,
            current_price   INTEGER DEFAULT 0,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS loans (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            institution     TEXT,
            principal       INTEGER DEFAULT 0,
            remaining       INTEGER DEFAULT 0,
            monthly_payment INTEGER DEFAULT 0,
            interest_rate   REAL DEFAULT 0,
            loan_date       TEXT,
            end_date        TEXT,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS pension (
            id              SERIAL PRIMARY KEY,
            pension_type    TEXT,
            name            TEXT,
            institution     TEXT,
            monthly_payment INTEGER DEFAULT 0,
            accumulated     INTEGER DEFAULT 0,
            return_rate     REAL DEFAULT 0,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS goals (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            target_amount   INTEGER DEFAULT 0,
            current_amount  INTEGER DEFAULT 0,
            monthly_saving  INTEGER DEFAULT 0,
            target_date     TEXT,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS cash_deposits (
            id              SERIAL PRIMARY KEY,
            name            TEXT,
            amount          INTEGER DEFAULT 0,
            memo            TEXT,
            updated_date    TEXT
        );

        CREATE TABLE IF NOT EXISTS card_mappings (
            id       SERIAL PRIMARY KEY,
            card_id  INTEGER UNIQUE REFERENCES card_info(id),
            mapping  TEXT
        );

        CREATE TABLE IF NOT EXISTS card_category_rules (
            id       SERIAL PRIMARY KEY,
            keyword  TEXT NOT NULL,
            category TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS categories (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS tenant_contracts (
            id              SERIAL PRIMARY KEY,
            real_estate_id  INTEGER NOT NULL REFERENCES real_estate(id),
            contract_type   TEXT NOT NULL,
            deposit         INTEGER NOT NULL DEFAULT 0,
            monthly_rent    INTEGER NOT NULL DEFAULT 0,
            start_date      TEXT,
            end_date        TEXT,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS property_costs (
            id              SERIAL PRIMARY KEY,
            real_estate_id  INTEGER NOT NULL REFERENCES real_estate(id),
            cost_type       TEXT NOT NULL,
            name            TEXT NOT NULL,
            amount          INTEGER NOT NULL DEFAULT 0,
            date            TEXT,
            memo            TEXT
        );

        CREATE TABLE IF NOT EXISTS fund_groups (
            id         SERIAL PRIMARY KEY,
            name       TEXT NOT NULL UNIQUE,
            sort_order INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS fund_group_rules (
            id            SERIAL PRIMARY KEY,
            keyword       TEXT NOT NULL,
            fund_group_id INTEGER NOT NULL REFERENCES fund_groups(id)
        );

        CREATE TABLE IF NOT EXISTS monthly_fund_budgets (
            id            SERIAL PRIMARY KEY,
            fund_group_id INTEGER NOT NULL REFERENCES fund_groups(id),
            year          INTEGER NOT NULL,
            month         INTEGER NOT NULL,
            budget_amount INTEGER NOT NULL DEFAULT 0,
            UNIQUE(fund_group_id, year, month)
        );
    """)

    # 마이그레이션: 기존 DB에 컬럼 추가 / 데이터 이전 (PostgreSQL 문법에 맞게 수정)
    migration_sqls = [
        "ALTER TABLE budget   ADD COLUMN IF NOT EXISTS card_id          INTEGER REFERENCES card_info(id)",
        "ALTER TABLE card_tx  ADD COLUMN IF NOT EXISTS budget_id         INTEGER REFERENCES budget(id)",
        "ALTER TABLE card_tx  ADD COLUMN IF NOT EXISTS category_locked   INTEGER DEFAULT 0",
        "ALTER TABLE card_tx  ADD COLUMN IF NOT EXISTS fund_group_id     INTEGER REFERENCES fund_groups(id)",
        "ALTER TABLE card_tx  ADD COLUMN IF NOT EXISTS fund_group_locked INTEGER DEFAULT 0",
        # 기본 카테고리 삽입 (INSERT OR IGNORE -> ON CONFLICT DO NOTHING)
        *[f"INSERT INTO categories (name, sort_order) VALUES ('{n}', {i}) ON CONFLICT (name) DO NOTHING"
          for i, n in enumerate(['식비', '쇼핑', '교통', '의료', '문화', '기타'])],
    ]

    for sql in migration_sqls:
        try:
            c.execute(sql)
        except Exception as e:
            print(f"Migration skip: {e}")
            conn.rollback()
            continue

    serial_tables = [
        'income',
        'budget',
        'card_info',
        'card_tx',
        'stocks',
        'stock_tx',
        'etf',
        'crypto',
        'residence',
        'real_estate',
        'loans',
        'pension',
        'goals',
        'cash_deposits',
        'card_mappings',
        'card_category_rules',
        'categories',
        'tenant_contracts',
        'property_costs',
        'fund_groups',
        'fund_group_rules',
        'monthly_fund_budgets',
    ]
    for table in serial_tables:
        try:
            c.execute(
                """
                SELECT setval(
                    pg_get_serial_sequence(%s, 'id'),
                    COALESCE((SELECT MAX(id) FROM """ + table + """), 0) + 1,
                    false
                )
                """,
                (table,)
            )
        except Exception as e:
            print(f"Sequence sync skip ({table}): {e}")
            conn.rollback()
            continue

    conn.commit()
    conn.close()
