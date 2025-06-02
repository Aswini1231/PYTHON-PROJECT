from flask import Flask, render_template, request, redirect, url_for, session, flash
import sqlite3
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime
import os
from uuid import uuid4
import logging

# Configure logging
logging.basicConfig(level=logging.DEBUG)

app = Flask(__name__)
app.secret_key = 'your_secret_key'
UPLOAD_FOLDER = 'static/uploads'
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

# Database initialization and migration
def init_db():
    with sqlite3.connect('od_tracker.db') as conn:
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password TEXT NOT NULL,
            role TEXT NOT NULL,
            email TEXT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS od_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_id INTEGER,
            student_name TEXT,
            phone_no TEXT,
            place TEXT,
            registration_proof TEXT,
            participation_proof TEXT,
            status TEXT DEFAULT 'Pending',
            submitted_at TEXT,
            event_date TEXT,
            FOREIGN KEY (student_id) REFERENCES users(id)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS od_limits (
            student_id INTEGER PRIMARY KEY,
            total_od INTEGER DEFAULT 14,
            used_od INTEGER DEFAULT 0,
            year INTEGER DEFAULT 2025,
            FOREIGN KEY (student_id) REFERENCES users(id)
        )''')
        # Check if event_date column exists in od_requests
        c.execute("PRAGMA table_info(od_requests)")
        columns = [col[1] for col in c.fetchall()]
        if 'event_date' not in columns:
            c.execute('ALTER TABLE od_requests ADD COLUMN event_date TEXT')
        # Check if year column exists in od_limits
        c.execute("PRAGMA table_info(od_limits)")
        columns = [col[1] for col in c.fetchall()]
        if 'year' not in columns:
            c.execute('ALTER TABLE od_limits ADD COLUMN year INTEGER DEFAULT 2025')
        # Update existing od_limits to total_od = 14
        c.execute('UPDATE od_limits SET total_od = 14 WHERE total_od != 14')
        conn.commit()

# Check and reset OD limit based on year
def check_and_reset_od_limit(student_id):
    current_year = datetime.now().year
    with sqlite3.connect('od_tracker.db') as conn:
        c = conn.cursor()
        c.execute('SELECT year, used_od FROM od_limits WHERE student_id = ?', (student_id,))
        result = c.fetchone()
        if result and result[0] != current_year:
            c.execute('UPDATE od_limits SET used_od = 0, year = ? WHERE student_id = ?', (current_year, student_id))
            conn.commit()
            logging.debug(f"Reset OD limit for student_id {student_id} to year {current_year}")

# Check allowed file extensions
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Home route
@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

# Register route
@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        role = request.form['role']
        email = request.form['email']
        hashed_password = generate_password_hash(password, method='pbkdf2:sha256')
        
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            try:
                c.execute('INSERT INTO users (username, password, role, email) VALUES (?, ?, ?, ?)',
                         (username, hashed_password, role, email))
                if role == 'student':
                    c.execute('INSERT INTO od_limits (student_id, total_od, used_od, year) VALUES (?, 14, 0, ?)',
                             (c.lastrowid, datetime.now().year))
                conn.commit()
                flash('Registration successful! Please login.')
                return redirect(url_for('login'))
            except sqlite3.IntegrityError:
                flash('Username already exists!')
    return render_template('register.html')

# Login route
@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            c.execute('SELECT * FROM users WHERE username = ?', (username,))
            user = c.fetchone()
            if user and check_password_hash(user[2], password):
                session['user_id'] = user[0]
                session['username'] = user[1]
                session['role'] = user[3]
                return redirect(url_for('dashboard'))
            flash('Invalid credentials!')
    return render_template('login.html')

# Dashboard route
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    with sqlite3.connect('od_tracker.db') as conn:
        c = conn.cursor()
        if session['role'] == 'student':
            # Check and reset OD limit
            check_and_reset_od_limit(session['user_id'])
            c.execute('SELECT * FROM od_requests WHERE student_id = ?', (session['user_id'],))
            od_requests = c.fetchall()
            logging.debug(f"Student OD requests: {od_requests}")
            c.execute('SELECT total_od, used_od FROM od_limits WHERE student_id = ?', (session['user_id'],))
            od_limit = c.fetchone()
            return render_template('student_dashboard.html', od_requests=od_requests, od_limit=od_limit)
        else:
            c.execute('SELECT * FROM od_requests')
            od_requests = c.fetchall()
            logging.debug(f"Teacher OD requests: {od_requests}")
            return render_template('teacher_dashboard.html', od_requests=od_requests)

# Submit OD request
@app.route('/submit_od', methods=['GET', 'POST'])
def submit_od():
    if 'user_id' not in session or session['role'] != 'student':
        return redirect(url_for('login'))
    
    if request.method == 'POST':
        # Check and reset OD limit
        check_and_reset_od_limit(session['user_id'])
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            c.execute('SELECT total_od, used_od FROM od_limits WHERE student_id = ?', (session['user_id'],))
            od_limit = c.fetchone()
            if od_limit[1] >= od_limit[0]:
                flash('You have reached your OD limit for this year!')
                return redirect(url_for('dashboard'))
        
        student_name = request.form['student_name']
        phone_no = request.form['phone_no']
        place = request.form['place']
        event_date = request.form['event_date']
        registration_proof = request.files['registration_proof']
        
        reg_proof_path = None
        
        if registration_proof and allowed_file(registration_proof.filename):
            filename = str(uuid4()) + '.' + registration_proof.filename.rsplit('.', 1)[1].lower()
            reg_proof_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            registration_proof.save(reg_proof_path)
        else:
            flash('Invalid or missing registration proof! Please upload a PNG, JPG, or JPEG file.')
            return redirect(url_for('submit_od'))
        
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            c.execute('INSERT INTO od_requests (student_id, student_name, phone_no, place, registration_proof, participation_proof, submitted_at, event_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
                     (session['user_id'], student_name, phone_no, place, reg_proof_path, None, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), event_date))
            conn.commit()
            flash('OD request submitted successfully!')
            return redirect(url_for('dashboard'))
    
    return render_template('submit_od.html')

# Upload participation proof after approval
@app.route('/upload_participation_proof/<int:od_id>', methods=['GET', 'POST'])
def upload_participation_proof(od_id):
    if 'user_id' not in session or session['role'] != 'student':
        return redirect(url_for('login'))
    
    with sqlite3.connect('od_tracker.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM od_requests WHERE id = ? AND student_id = ?', (od_id, session['user_id']))
        od_request = c.fetchone()
        
        if not od_request:
            flash('OD request not found or you do not have permission!')
            return redirect(url_for('dashboard'))
        if od_request[7] != 'Approved':
            flash('Participation proof can only be uploaded for approved OD requests!')
            return redirect(url_for('dashboard'))
        if od_request[6]:
            flash('Participation proof already uploaded!')
            return redirect(url_for('dashboard'))
        
        # Check if current date is after event_date
        try:
            event_date = datetime.strptime(od_request[9], '%Y-%m-%d')
            current_date = datetime.now()
            if current_date.date() <= event_date.date():
                flash('Participation proof can only be uploaded after the event date!')
                return redirect(url_for('dashboard'))
        except (TypeError, ValueError):
            flash('Event date is invalid or missing! Please contact support.')
            return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        participation_proof = request.files.get('participation_proof')
        
        if participation_proof and allowed_file(participation_proof.filename):
            filename = str(uuid4()) + '.' + participation_proof.filename.rsplit('.', 1)[1].lower()
            part_proof_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            participation_proof.save(part_proof_path)
            
            with sqlite3.connect('od_tracker.db') as conn:
                c = conn.cursor()
                c.execute('UPDATE od_requests SET participation_proof = ? WHERE id = ?', (part_proof_path, od_id))
                conn.commit()
                flash('Participation proof uploaded successfully!')
                return redirect(url_for('dashboard'))
        else:
            flash('Invalid file format! Please upload a PNG, JPG, or JPEG file.')
    
    return render_template('upload_participation_proof.html', od_request=od_request)

# Approve/Reject OD request
@app.route('/manage_od/<int:od_id>', methods=['GET', 'POST'])
def manage_od(od_id):
    if 'user_id' not in session or session['role'] != 'teacher':
        return redirect(url_for('login'))
    
    with sqlite3.connect('od_tracker.db') as conn:
        c = conn.cursor()
        c.execute('SELECT * FROM od_requests WHERE id = ?', (od_id,))
        od_request = c.fetchone()
        
        if not od_request:
            flash('OD request not found!')
            return redirect(url_for('dashboard'))
        if od_request[7] != 'Pending':
            flash('This OD request has already been processed!')
            return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        action = request.form['action']
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            if action == 'approve':
                # Check and reset OD limit for the student
                check_and_reset_od_limit(od_request[1])
                c.execute('SELECT used_od, total_od FROM od_limits WHERE student_id = ?', (od_request[1],))
                od_limit = c.fetchone()
                if od_limit[0] >= od_limit[1]:
                    flash('Student has reached their OD limit for this year!')
                    return redirect(url_for('dashboard'))
                c.execute('UPDATE od_requests SET status = ? WHERE id = ?', ('Approved', od_id))
                c.execute('UPDATE od_limits SET used_od = used_od + 1 WHERE student_id = (SELECT student_id FROM od_requests WHERE id = ?)', (od_id,))
                flash('OD request approved successfully!')
            elif action == 'reject':
                c.execute('UPDATE od_requests SET status = ? WHERE id = ?', ('Rejected', od_id))
                flash('OD request rejected successfully!')
            else:
                flash('Invalid action selected!')
            conn.commit()
        return redirect(url_for('dashboard'))
    
    return render_template('manage_od.html', od_request=od_request)

# Search OD history
@app.route('/search', methods=['GET', 'POST'])
def search():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    results = []
    if request.method == 'POST':
        search_term = request.form['search_term']
        with sqlite3.connect('od_tracker.db') as conn:
            c = conn.cursor()
            if session['role'] == 'student':
                c.execute('SELECT * FROM od_requests WHERE student_id = ? AND (student_name LIKE ? OR place LIKE ?)',
                         (session['user_id'], f'%{search_term}%', f'%{search_term}%'))
            else:
                c.execute('SELECT * FROM od_requests WHERE student_name LIKE ? OR place LIKE ?',
                         (f'%{search_term}%', f'%{search_term}%'))
            results = c.fetchall()
    return render_template('search.html', results=results)

# Logout route
@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

if __name__ == '__main__':
    os.makedirs(UPLOAD_FOLDER, exist_ok=True)
    init_db()
    app.run(debug=True)