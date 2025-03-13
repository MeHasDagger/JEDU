
from flask import Flask, render_template, request, jsonify, send_file
from flask_apscheduler import APScheduler
from datetime import date, timedelta
import sqlite3
import os
import random

  
app = Flask(__name__) 
scheduler = APScheduler()

FILE_DIRECTORY = "files_upload"
FILE_PAGE_ADDRESS = "http://127.0.0.1:5000/file/"
DAYS_UNTIL_FILE_REMOVAL = 10
os.makedirs(FILE_DIRECTORY, exist_ok=True)

@scheduler.task('interval', id='do_file_removal', days=2, misfire_grace_time=900)
def scheduled_file_removal():
    delete_old_files()

scheduler.start()


@app.route('/') 
@app.route('/home') 
def index(): 
    return render_template('index.html') 

# Initiate database
connect = sqlite3.connect('web_files.db') 
connect.execute( 
    'CREATE TABLE IF NOT EXISTS Files ( \
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        hex_code TEXT NOT NULL UNIQUE, \
        file_path TEXT NOT NULL UNIQUE, \
        create_date INTEGER NOT NULL)') 
connect.commit()
connect.close()

# Takes in the upload from the JEDU application and saves it to the database and the disk
@app.route('/save', methods=['POST'])
def upload_file():
    # Makes sure file was sent
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']

    # Makes sure the filename isn't empty
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Save the file to disk and make sure name is unique
    file_path = save_unique_file(file)

    conn = sqlite3.connect("web_files.db")
    cur = conn.cursor()
    hex_code = generate_unique_hex(cur)
  
    current_date = date.today().strftime("%Y%m%d")

    # Save the file data to the database
    try:
        cur.execute('INSERT INTO files (hex_code, file_path, create_date) VALUES (?, ?, ?)', (hex_code, file_path, current_date))
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"file name already taken: {e}")
        return jsonify({'error': 'Error uploading file'}), 400
    finally:
        conn.close()
    full_download_page_address = FILE_PAGE_ADDRESS + hex_code
    return jsonify({'message': f'File uploaded and saved successfully. Here is your link: {full_download_page_address}'}), 201

# Generates a unique hex code for the files
def generate_unique_hex(cur):
    while True:
        # Creates a 6 digit long hexadecimal string
        hex = '%6x' % random.getrandbits(24)
        cur.execute('SELECT hex_code FROM Files WHERE hex_code = ?', (hex,))

        # Checks if the cursor doesn't have a value or if it does
        if cur.fetchone():
            print("Another hex_code found while generating unique hex")
        else:
            return hex

# Makes sure there is only one filename with the same name
def save_unique_file(file):
    file_name = file.filename
    name, ext = os.path.splitext(file_name)

    counter = 1
    while os.path.exists(os.path.join(FILE_DIRECTORY, f"{file_name}")):
        file_name = os.path.join(f"{name}({counter}){ext}")
        counter += 1

    file.save(os.path.join(FILE_DIRECTORY, f"{file_name}"))
    return file_name

# Fetches all the files in the database
@app.route('/files') 
def files(): 
    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 
    cur.execute('SELECT * FROM Files') 
    data = cur.fetchall() 
    conn.close()
    return render_template("files.html", data=data)  


# The download page, takes in a hex code as a parameter 
# and then sends the file assosiated with that hex code
@app.route('/file/<hex_code>') 
def download(hex_code):
    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 
    cur.execute('SELECT file_path FROM Files WHERE hex_code = ?', (hex_code,))
    filenames = cur.fetchone()
    conn.close()
    # Checks if the cursor got a match and if so changes to another html page
    if filenames: 
        # Ignores the list and only gets the first element
        filename = filenames[0]
        return render_template("downloadfile.html", filename=filename)    
    else:
        return jsonify({'error': 'No file with that hex'}), 400

# The actual file download that gets accessesed by the javascript on the download page.
@app.route('/download', methods=['POST'])
def download_file():
    # Get the string from the request data
    data = request.json
    file_name = data.get('fileName')

    # Appends the FILE_DIRECTORY and sends the file
    file_path = os.path.join(FILE_DIRECTORY, f"{file_name}")
    return send_file(file_path, as_attachment=True) 

def delete_old_files():
    print("Running removal of old files ")
   
    file_life_span = (date.today() - timedelta(days=DAYS_UNTIL_FILE_REMOVAL)).strftime('%Y%m%d')

    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 

    cur.execute('SELECT * FROM Files WHERE create_date < ?', (file_life_span,))
    data = cur.fetchall()
    print("Deleted files:" + data)

    cur.execute('DELETE FROM Files WHERE create_date < ?', (file_life_span,))
    conn.commit()
    conn.close()

# @@@@@@@@@@@@@@@@ REMOVE LATER @@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@@
@app.route('/test', methods=['POST', 'GET'])
def change_date(): 
    date = request.args.get('date', default = 20250103, type = int)
    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 
    cur.execute('SELECT hex_code FROM Files') 
    data = cur.fetchone() 
    if data: 
        # Ignores the list and only gets the first element
        data2 = data[0]
        print(data2)
        print(date)
        cur.execute('UPDATE Files SET create_date = ? WHERE hex_code = ?', (date, data2)) 
        conn.commit()

    conn.close()
    return jsonify({'Message': 'No error, maybe'}), 400

if __name__ == '__main__': 
    app.run(debug=False) 
    