from datetime import date, timedelta
import sqlite3

print("Running removal of old files")

conn = sqlite3.connect('web_files.db') 
cur = conn.cursor() 

#current_date = date.today().strftime("%Y%m%d")
thirty_days_ago = (date.today() - timedelta(days=30)).strftime('%Y%m%d')

cur.execute("DELETE FROM Files WHERE create_date < ?", (thirty_days_ago,))
conn.commit()
conn.close()

