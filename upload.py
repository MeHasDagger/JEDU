from flask import Flask, render_template, request, jsonify, send_file, g
from flask_apscheduler import APScheduler
from datetime import date, timedelta
from dotenv import load_dotenv
import sqlite3
import os
import random
import smtplib # For email sending
from email.mime.text import MIMEText # For creating email messages
# ADDED: Import for securing filenames
from werkzeug.utils import secure_filename

# --- Configuration ---
app = Flask(__name__)
# Initialize the scheduler
scheduler = APScheduler()

# Load environment variables
dotenv_path = os.path.join(os.path.dirname(__file__), 'secret_keys.env')
load_dotenv(dotenv_path)

# Get config from environment or use defaults
UPLOAD_KEY = os.getenv('UPLOAD_KEY', 'DEFAULT_SUPER_SECRET_KEY') # Add a default if missing
DATABASE_URL = os.getenv('DATABASE_URL', 'web_files.db') # Default database name
FILE_DIRECTORY = os.getenv('FILE_DIRECTORY', "files_upload")
FILE_PAGE_ADDRESS = os.getenv('FILE_PAGE_ADDRESS', "http://127.0.0.1:5000/file")
DAYS_UNTIL_FILE_REMOVAL = int(os.getenv('DAYS_UNTIL_FILE_REMOVAL', 10))

# Email configuration from .env
SMTP_SERVER = os.getenv('SMTP_SERVER')
SMTP_PORT = int(os.getenv('SMTP_PORT', 587)) # Default port for TLS
SMTP_USER = os.getenv('SMTP_USER')
SMTP_PASSWORD = os.getenv('SMTP_PASSWORD')
EMAIL_SENDER = os.getenv('EMAIL_SENDER', SMTP_USER) # Sender address defaults to user

# Ensure upload directory exists
os.makedirs(FILE_DIRECTORY, exist_ok=True)

# --- Database Handling ---
def get_db():
    """Opens a new database connection if one isn't already open for the current request context."""
    if 'db' not in g:
        # NOTE: Ensure connection uses PARSE_DECLTYPES and PARSE_COLNAMES for date handling etc.
        g.db = sqlite3.connect(DATABASE_URL, detect_types=sqlite3.PARSE_DECLTYPES | sqlite3.PARSE_COLNAMES)
        # NOTE: Returns rows as dictionary-like objects (access columns by name)
        g.db.row_factory = sqlite3.Row
    return g.db

@app.teardown_appcontext
def close_db(exception):
    """Closes the database connection at the end of the request."""
    db = g.pop('db', None)
    if db is not None:
        db.close()

def init_db():
    """Initializes the database table if it doesn't exist."""
    db = get_db()
    cursor = db.cursor()
    # NOTE: --- CORRECTED SCHEMA ---
    # Uses separate columns for original and stored filenames, adds email.
    cursor.execute(
        '''CREATE TABLE IF NOT EXISTS Files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            hex_code TEXT NOT NULL UNIQUE,
            original_filename TEXT NOT NULL,
            stored_filename TEXT NOT NULL UNIQUE,
            create_date DATE NOT NULL,
            email TEXT
        )'''
    )
    # NOTE: --- END CORRECTED SCHEMA ---
    db.commit()
    print("Database initialized.")

@app.before_request
def before_first_request_func():
    """Runs init_db once before the first request."""
    # Use an app context flag to run only once
    if not hasattr(g, 'db_initialized'):
        with app.app_context():
            init_db()
            g.db_initialized = True


# --- Scheduler ---
# NOTE: --- CHANGED days= TO USE VARIABLE // change how long files should be saved before they are deleted simply by modifying 
# the value of DAYS_UNTIL_FILE_REMOVAL in your secret_keys.env file  ---
@scheduler.task('interval', id='do_file_removal', days=DAYS_UNTIL_FILE_REMOVAL, misfire_grace_time=900)
def scheduled_file_removal():
    """Scheduled job to delete old files."""
    print("Scheduled task started: Removing old files...")
    # Requires app context for database access
    with app.app_context():
        delete_old_files()
    print("Scheduled task finished.")

# Initialize and start the scheduler after app configuration
scheduler.init_app(app)
scheduler.start()


@app.route('/')
@app.route('/home')
def index():
    """Displays the home page."""
    return render_template('index.html')

@app.route('/save', methods=['POST'])
def upload_file():
    """Handles file upload, saves file and data, sends confirmation email."""
    # Check file part exists
    if 'file' not in request.files:
        return jsonify({'error': 'No file part'}), 400
    file = request.files['file']
    if not file or file.filename == '':
        return jsonify({'error': 'No selected file'}), 400

    # Check authorization header
    auth_header = request.headers.get('Authorization')
    if not auth_header or not auth_header.startswith('Bearer '):
        return jsonify({'error': 'Authorization header missing or invalid format'}), 401
    received_key = auth_header.split(' ')[1]
    if received_key != UPLOAD_KEY:
        return jsonify({'error': 'Invalid Authorization key'}), 401

    # Get email address (from form data, for terminal script)
    email = request.form.get('email')
    if not email:
         # NOTE: Allow uploads without email, set to None
         print("Warning: No email provided for uploaded file.")
         email = None

    # NOTE: --- CHANGED FILE HANDLING and Database INSERT ---
    file_path_on_disk = None
    original_filename_unsafe = file.filename
    try:
        # NOTE: save_unique_file now returns path, stored name, AND safe original name
        file_path_on_disk, stored_filename, original_filename_safe = save_unique_file(file)

    except Exception as e:
        print(f"Error saving file '{original_filename_unsafe}' to disk: {e}")
        return jsonify({'error': 'Error saving file to disk'}), 500

    # Generate unique hex code and save to database
    conn = get_db()
    cur = conn.cursor()
    hex_code = generate_unique_hex(cur)
    current_date = date.today() # Use date object directly

    try:
        # NOTE: Insert using the corrected schema columns and safe names
        cur.execute(
            '''INSERT INTO Files (hex_code, original_filename, stored_filename, create_date, email)
               VALUES (?, ?, ?, ?, ?)''',
            (hex_code, original_filename_safe, stored_filename, current_date, email) # Saves correct data
        )
        conn.commit()
        print(f"UPLOAD SUCCESS: Metadata saved for '{original_filename_safe}' (Stored: '{stored_filename}', Hex: {hex_code}, Email: {email})")
    except sqlite3.Error as e:
        print(f"Database error during insert: {e}")
        cleanup_file(file_path_on_disk) # Attempt to cleanup file
        return jsonify({'error': 'Error saving file metadata to database'}), 500
    except Exception as e:
        print(f"Unexpected error during database operation: {e}")
        cleanup_file(file_path_on_disk)
        return jsonify({'error': 'An unexpected server error occurred'}), 500
    # NOTE: --- END CHANGED FILE HANDLING and Database INSERT ---

    # Send confirmation email (if email was provided)
    if email:
        try:
            # Pass the safe original filename
            send_confirmation_email(email, original_filename_safe, hex_code)
            print(f"Confirmation email sent to {email}")
        except Exception as e:
            # Log email failure but don't break the upload response
            print(f"EMAIL WARNING: Failed to send confirmation email to {email}: {e}")

    # Create download link
    # NOTE: Ensure FILE_PAGE_ADDRESS does not end with a slash
    full_download_page_address = f"{FILE_PAGE_ADDRESS.rstrip('/')}/{hex_code}"
    return jsonify({'message': f'File uploaded successfully. Download link: {full_download_page_address}'}), 201

@app.route('/files')
def list_files():
    """Displays a list of uploaded files."""
    conn = get_db()
    cur = conn.cursor()
    # Select the columns you want to display
    # NOTE: --- CHANGED SELECT TO FETCH original_filename ---
    cur.execute('SELECT hex_code, original_filename, create_date FROM Files ORDER BY create_date DESC')
    files_data = cur.fetchall()

    # NOTE: Pass correct variable name ('files') and base URL to the template
    return render_template("files.html", files=files_data, file_page_address=FILE_PAGE_ADDRESS)

# NOTE: --- CORRECTED - Download Routes ---

@app.route('/file/<string:hex_code>')
def download_page_v1(hex_code):
    """Displays the download page for a specific file (Version 1)."""
    conn = get_db()
    cur = conn.cursor()
    # NOTE: --- CHANGED SELECT TO FETCH original_filename ---
    cur.execute('SELECT original_filename FROM Files WHERE hex_code = ?', (hex_code,))
    file_record = cur.fetchone()

    if file_record:
        # Pass original_filename to the template as 'filename'
        # Also pass hex_code if your JS needs it
        return render_template("downloadfile.html", filename=file_record['original_filename'], hex_code=hex_code)
    else:
        # Use a 404 template if available
        return render_template("404.html", message=f"No file found with code: {hex_code}"), 404

# NOTE: --- CHANGED /download ---
@app.route('/download', methods=['POST'])
def download_file_v1():
    """Handles POST from JS, sends the file (Version 1)."""
    if not request.is_json: return jsonify({'error': 'Request must be JSON'}), 400
    data = request.get_json()
    # Original filename sent by JS
    original_filename_from_post = data.get('fileName')
    if not original_filename_from_post: return jsonify({'error': 'Missing "fileName" in JSON body'}), 400

    # NOTE: --- CORRECTED LOGIC: Find stored_filename based on original_filename ---
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute('SELECT stored_filename FROM Files WHERE original_filename = ? ORDER BY create_date DESC LIMIT 1', (original_filename_from_post,))
        file_record = cur.fetchone()

        if file_record:
            stored_filename = file_record['stored_filename']
            file_path = os.path.join(FILE_DIRECTORY, stored_filename)
            if os.path.exists(file_path):
                # Send the file, suggest original name to the user
                return send_file(file_path, as_attachment=True, download_name=original_filename_from_post)
            else:
                 print(f"DOWNLOAD ERROR V1: File missing on disk: {file_path}")
                 return jsonify({'error': 'File not found on server storage'}), 404
        else:
            return jsonify({'error': f'No file record found for filename: {original_filename_from_post}'}), 404
    except sqlite3.Error as e:
        print(f"DOWNLOAD V1 DB ERROR for '{original_filename_from_post}': {e}")
        return jsonify({'error': 'Database error during download lookup'}), 500
    except Exception as e:
        print(f"DOWNLOAD V1 UNEXPECTED ERROR for '{original_filename_from_post}': {e}")
        return jsonify({'error': 'An unexpected server error occurred'}), 500


def generate_unique_hex(cursor):
    """Generates a unique 6-digit hex code."""
    while True:
        hex_code = '%06x' % random.getrandbits(24) # Ensure 6 digits with padding
        cursor.execute('SELECT hex_code FROM Files WHERE hex_code = ?', (hex_code,))
        if not cursor.fetchone():
            return hex_code

# NOTE: --- CORRECTING save_unique_file ---
def save_unique_file(file):
    """Saves the file to disk with a unique name if necessary, returns path, stored name, and safe original name."""
    # NOTE: --- USE secure_filename ---
    original_filename_safe = secure_filename(file.filename)
    original_filename_safe = original_filename_safe or "uploaded_file" # Fallback name

    name, ext = os.path.splitext(original_filename_safe)
    stored_filename = original_filename_safe # Start with the safe name
    counter = 1
    full_path = os.path.join(FILE_DIRECTORY, stored_filename)

    # Loop until a free filename is found
    while os.path.exists(full_path):
        stored_filename = f"{name}({counter}){ext}"
        full_path = os.path.join(FILE_DIRECTORY, stored_filename)
        counter += 1
        if counter > 1000: # Safety break
            raise Exception(f"Could not find unique filename for {original_filename_safe}")

    file.save(full_path)
    # NOTE: Return all three values
    return full_path, stored_filename, original_filename_safe
# NOTE: --- END CORRECTING save_unique_file ---

# NOTE: --- CORRECTING delete_old_files ---
def delete_old_files():
    """Deletes files and database records older than DAYS_UNTIL_FILE_REMOVAL."""
    # NOTE: Use DATE object for comparison
    cutoff_date = date.today() - timedelta(days=DAYS_UNTIL_FILE_REMOVAL)
    conn = get_db()
    cur = conn.cursor()

    try:
        # NOTE: --- CORRECTING SELECT TO FETCH stored_filename ---
        # Fetch files to delete
        cur.execute('SELECT id, stored_filename FROM Files WHERE create_date < ?', (cutoff_date,))
        files_to_delete = cur.fetchall()

        deleted_db_count = 0
        deleted_file_count = 0
        failed_file_deletions = []

        if not files_to_delete:
             print(f"No files found older than {cutoff_date.strftime('%Y-%m-%d')}.")
             return

        print(f"Found {len(files_to_delete)} records older than {cutoff_date.strftime('%Y-%m-%d')}. Deleting...")

        for record in files_to_delete:
            file_id = record['id']
            # NOTE: --- USE stored_filename ---
            stored_filename = record['stored_filename']
            file_path_on_disk = os.path.join(FILE_DIRECTORY, stored_filename)

            # Delete Database record first (or after file, depending on preference)
            try:
                cur.execute('DELETE FROM Files WHERE id = ?', (file_id,))
                conn.commit() # Commit after each successful Database delete
                deleted_db_count += 1

                # Attempt to delete file from disk if Database deletion was successful
                try:
                    if os.path.exists(file_path_on_disk):
                        os.remove(file_path_on_disk)
                        print(f"Deleted file from disk: {stored_filename}")
                        deleted_file_count += 1
                    else:
                        print(f"File not found on disk (already deleted?): {stored_filename}")
                except OSError as e:
                    print(f"Error deleting file {stored_filename} from disk: {e}")
                    failed_file_deletions.append(stored_filename)                    

            except sqlite3.Error as e:
                 print(f"Error deleting record with id {file_id} from database: {e}")
                 conn.rollback() # Rollback failed DB delete

        print(f"Deletion complete. Removed {deleted_db_count} DB records and {deleted_file_count} files from disk.")
        if failed_file_deletions:
             print(f"WARNING: Failed to delete files: {', '.join(failed_file_deletions)}")

    except sqlite3.Error as e:
        print(f"Database error during file deletion process: {e}")
        if conn: conn.rollback() # Ensure rollback on general Database error
# NOTE: --- END CORRECTED delete_old_files ---

def send_confirmation_email(recipient_email, filename, hex_code):
    """Sends a confirmation email to the user."""
    # NOTE: Check if all necessary email config variables are set
    if not all([SMTP_SERVER, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, EMAIL_SENDER]):
        print("Email configuration missing in .env file. Cannot send confirmation email.")
        # NOTE: Raise error to indicate config issue clearly in logs
        raise ValueError("Email configuration missing.")

    sender_email = EMAIL_SENDER
    receiver_email = recipient_email
    password = SMTP_PASSWORD

    # NOTE: Ensure correct link construction (no double slashes)
    download_link = f"{FILE_PAGE_ADDRESS.rstrip('/')}/{hex_code}"

    message_body = f"""Hello!

Your file "{filename}" has been uploaded and saved.

You can access it via this link: {download_link}

The link and the file will be deleted in {DAYS_UNTIL_FILE_REMOVAL} days.

Best regards,
Your File Upload Service
"""

    # NOTE: Create the email body using MIMEText, Specify encoding for the message, utf-8 for handling non-ascii characters (åäö, symbols etc)
    message = MIMEText(message_body, 'plain', 'utf-8')
    message['Subject'] = f'File Upload Confirmation: {filename}'
    message['From'] = sender_email
    message['To'] = receiver_email

    try:
        # Connect to SMTP server and send 
        # Example for TLS (most common)
        context = smtplib.ssl.create_default_context()
        with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
            server.ehlo() # Identify client and discover server capabilities/ starttls 
            server.starttls(context=context)
            server.ehlo() # Discover updated capabilities/ AUTH methods etc
            server.login(SMTP_USER, password)
            server.sendmail(sender_email, receiver_email, message.as_string())
        print(f"Successfully sent email to {receiver_email}")

    except smtplib.SMTPAuthenticationError:
         print(f"SMTP Authentication Error: Check username/password for {SMTP_USER}.")
         raise # Re-throw exception
    except smtplib.SMTPConnectError:
        print(f"SMTP Connect Error: Could not connect to {SMTP_SERVER}:{SMTP_PORT}.")
        raise
    except Exception as e:
        print(f"An unexpected error occurred while sending email: {e}")
        raise

# --- App Startup ---
if __name__ == '__main__':
    print("Starting Flask application server (Corrected Version 1)...")
    # NOTE: init_db runs via before_request hook
    app.run(host='0.0.0.0', port=5000, debug=False) # Turn off debug in production
