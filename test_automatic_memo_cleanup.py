from database import automatic_mail_memo_cleanup_sql


def test_cleanup_only_targets_source_key_rows_with_exact_automatic_patterns():
    sql = automatic_mail_memo_cleanup_sql()

    assert "INSERT INTO app_migrations" in sql
    assert "ON CONFLICT (migration_key) DO NOTHING" in sql
    assert "EXISTS (SELECT 1 FROM applied)" in sql
    assert "source_key IS NOT NULL" in sql
    assert "memo ~ '^\\[메일자동등록\\] [^|]+$'" in sql
    assert "memo ~ '^\\[쿠팡\\] .+$'" in sql
    assert "memo ~ '^\\[메일자동등록\\] [^|]+ \\| \\[쿠팡\\] .+$'" in sql
