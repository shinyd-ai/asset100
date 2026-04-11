import sqlite3
db = 'finance.db'
conn = sqlite3.connect(db)
# Try both formats just in case
rows = conn.execute('select distinct strftime("%Y-%m", date) from card_tx').fetchall()
print("Year-Month in records:", rows)
cards = conn.execute('select id, card_name from card_info').fetchall()
print("Cards in records:", [dict(r) for r in cards] if cards else "None")
conn.close()
