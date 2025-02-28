
from flask import Flask, render_template, request, jsonify, send_file
import sqlite3
import os
import random
  
app = Flask(__name__) 
  
FILE_DIRECTORY = "files_upload"

@app.route('/') 
@app.route('/home') 
def index(): 
    return render_template('index.html') 
    
# Initiate database
connect = sqlite3.connect('web_files.db') 
connect.execute( 
    'CREATE TABLE IF NOT EXISTS FILES ( \
        id INTEGER PRIMARY KEY AUTOINCREMENT, \
        hex_code TEXT NOT NULL UNIQUE, \
        file_path TEXT NOT NULL UNIQUE)') 

@app.route('/save', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400

    file = request.files['file']

    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Save the file to disk and make sure name is unique
    file_path = save_unique_file(file)

    conn = sqlite3.connect("web_files.db")
    cur = conn.cursor()
    hex_code = generate_unique_hex(cur)

    # Save the file data to the database
    try:
        cur.execute('INSERT INTO files (hex_code, file_path) VALUES (?, ?)', (hex_code, file_path))
        conn.commit()
    except sqlite3.IntegrityError as e:
        print(f"file name already taken: {e}")
        return jsonify({'message': 'Error uploading file'}), 400
    finally:
        conn.close()

    # Remove the temporary file
    #os.remove(file_path)

    return jsonify({'message': 'File uploaded and saved successfully'}), 201

def generate_unique_hex(cur):
    while True:
        hex = '%6x' % random.getrandbits(24)
        cur.execute('SELECT hex_code FROM Files WHERE hex_code = ?', (hex,))
        if cur.fetchall():
            print("Another hex_code found while generating unique hex")
        else:
            return hex

def save_unique_file(file):
    file_name = file.filename
    name, ext = os.path.splitext(file_name)

    counter = 1
    while os.path.exists(os.path.join(FILE_DIRECTORY, f"{file_name}")):
        file_name = os.path.join(f"{name}({counter}){ext}")
        counter += 1

    file.save(os.path.join(FILE_DIRECTORY, f"{file_name}"))
    return file_name


@app.route('/files') 
def files(): 
    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 
    cur.execute('SELECT * FROM FILES') 
    data = cur.fetchall() 
    conn.close()
    return render_template("files.html", data=data)  

@app.route('/file/<hex_code>') 
def download(hex_code):
    conn = sqlite3.connect('web_files.db') 
    cur = conn.cursor() 
    cur.execute('SELECT file_path FROM Files WHERE hex_code = ?', (hex_code,))
    filenames = cur.fetchone()
    if filenames: 
        filename = filenames[0]
        file_path = os.path.join(FILE_DIRECTORY, f"{filename}")
        return send_file(file_path, as_attachment=True)  
    else:
        return jsonify({'error': 'No file with that hex'}), 400

  
if __name__ == '__main__': 
    app.run(debug=False) 