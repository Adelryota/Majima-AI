import os
import sys
import warnings
import time
import bcrypt
from datetime import datetime
from functools import wraps
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, flash, redirect, url_for, session, g, jsonify, send_from_directory
from boto3.dynamodb.conditions import Key, Attr

# --- Import Pipelines ---
try:
    from ingestion_pipeline import process_and_store_lecture
    from retrieval_pipeline import retrieve_chunks_for_lecture
    from summarization_pipeline import run_single_shot_summary
    from db_dynamo import get_dynamodb_resource, delete_lecture_fully
except ImportError as e:
    print(f"FATAL ERROR: Missing pipeline files. {e}", file=sys.stderr)
    sys.exit(1)

# --- Configuration ---
UPLOAD_FOLDER = 'temp'
ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__)

# Custom Template Filter for Dates
@app.template_filter('datetime')
def format_datetime(value, format="%Y-%m-%d %H:%M"):
    if value is None: return ""
    try:
        return datetime.fromtimestamp(float(value)).strftime(format)
    except (ValueError, TypeError):
        return value

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
# Load secret key from environment variable
app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'dev-key-please-change-in-prod')
app.config['JSON_AS_ASCII'] = False

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

# =========================================
# SECURITY HEADERS
# =========================================
@app.after_request
def add_header(response):
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response

# =========================================
# DB HELPER
# =========================================
def get_db():
    if 'dynamo' not in g:
        g.dynamo = get_dynamodb_resource()
    return g.dynamo

# =========================================
# AUTHENTICATION
# =========================================
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
                 return jsonify(error='Session expired. Please log in again.'), 401
            flash('Please log in to access this page.', 'error')
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session: return redirect(url_for('login'))
        if session.get('role') != 'admin':
            flash('Access denied. Administrator privileges required.', 'error')
            return redirect(url_for('summarizer_dashboard'))
        return f(*args, **kwargs)
    return decorated_function

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# =========================================
# ROUTES
# =========================================
@app.route('/')
def index():
    if 'user_id' in session:
        if session.get('role') == 'admin':
            return redirect(url_for('admin_dashboard'))
        return redirect(url_for('summarizer_dashboard'))
    return render_template('intro.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        
        db = get_db()
        table = db.Table('Users')
        
        try:
            response = table.get_item(Key={'username': username})
            user = response.get('Item')
            
            if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
                if user['role'] == 'admin':
                     flash('Invalid username or password.', 'error') # Admins use separate login mostly, but simplified here
                     return redirect(url_for('login'))

                # Use username as ID since it's PK
                session['user_id'] = user['username'] 
                session['username'] = user['username']
                session['role'] = user['role']
                return redirect(url_for('index'))
            else:
                flash('Invalid username or password.', 'error')
        except Exception as e:
            flash(f"Login failed: {e}", 'error')

    if 'user_id' in session: return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/admin/login-auth', methods=['POST'])
def admin_login_auth():
    username = request.form.get('username')
    password = request.form.get('password')
    db = get_db()
    table = db.Table('Users')
    
    try:
        response = table.get_item(Key={'username': username})
        user = response.get('Item')

        if user and bcrypt.checkpw(password.encode('utf-8'), user['password_hash'].encode('utf-8')):
            if user['role'] == 'admin':
                session['user_id'] = user['username']
                session['username'] = user['username']
                session['role'] = 'admin'
                flash(f'Welcome Administrator, {user["username"]}!', 'success')
                return redirect(url_for('admin_dashboard'))
            else:
                 flash('Access denied.', 'error')
                 return redirect(url_for('login'))
        else:
             flash('Invalid admin credentials.', 'error')
             return redirect(url_for('login'))
    except Exception as e:
        flash(f"System Error: {e}", 'error')
        return redirect(url_for('login'))

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out.', 'success')
    return redirect(url_for('login'))

# =========================================
# ADMIN ROUTES
# =========================================
@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    db = get_db()
    try:
        # Scan count (inefficient but works for small app)
        s_count = db.Table('Subjects').scan(Select='COUNT')['Count']
        l_count = db.Table('Lectures').scan(Select='COUNT')['Count']
    except: s_count = 0; l_count = 0
    return render_template('admin_dashboard.html', subject_count=s_count, lecture_count=l_count)

@app.route('/admin/subjects')
@admin_required
def manage_subjects():
    db = get_db()
    data = []
    try:
        # 1. Get All Subjects
        subjects = db.Table('Subjects').scan().get('Items', [])
        subjects.sort(key=lambda x: x['name'])
        
        # 2. Get All Lectures (Scan) - optimize later with GSI if needed
        all_lectures = db.Table('Lectures').scan().get('Items', [])
        
        # 3. Join in Python
        for sub in subjects:
            # Filter lectures for this subject
            lectures = [l for l in all_lectures if l.get('subject_name') == sub['name']]
            # Dynamodb returns Decimal for numbers, but here we just have strings/metadata
            data.append({'id': sub['name'], 'name': sub['name'], 'lectures': lectures}) # Mapping name to ID
            
    except Exception as e: flash(f"DB Error: {e}", 'error')
    return render_template('manage_subjects.html', subjects_data=data)

@app.route('/admin/subject/add', methods=['POST'])
@admin_required
def add_subject():
    name = request.form.get('name')
    if name:
        try:
            db = get_db()
            # Check exist
            resp = db.Table('Subjects').get_item(Key={'name': name})
            if 'Item' in resp:
                flash(f"Subject '{name}' already exists.", 'error')
            else:
                db.Table('Subjects').put_item(Item={'name': name})
                flash(f"Subject '{name}' added.", 'success')
        except Exception as e: flash(f"Error: {e}", 'error')
    return redirect(url_for('manage_subjects'))

@app.route('/admin/subject/edit/<subject_id>', methods=['POST']) # subject_id is now the name string
@admin_required
def edit_subject(subject_id): # subject_id passed here is the OLD name
    new_name = request.form.get('new_name')
    if new_name and new_name != subject_id:
        try:
            db = get_db()
            # DynamoDB doesn't support PK update. Must Copy & Delete.
            # 1. Check if new name exists
            if 'Item' in db.Table('Subjects').get_item(Key={'name': new_name}):
                flash("Subject name taken.", 'error')
                return redirect(url_for('manage_subjects'))
            
            # 2. Create New
            db.Table('Subjects').put_item(Item={'name': new_name})
            
            # 3. Update all Lectures linked to old subject (manual cascade update)
            # This is slow, but necessary for NoSQL denormalization absent relation
            lectures = db.Table('Lectures').scan(FilterExpression=Attr('subject_name').eq(subject_id)).get('Items', [])
            for lect in lectures:
                db.Table('Lectures').update_item(
                    Key={'lecture_id': lect['lecture_id']},
                    UpdateExpression="set subject_name=:n",
                    ExpressionAttributeValues={':n': new_name}
                )
            
            # 4. Delete Old Subject
            db.Table('Subjects').delete_item(Key={'name': subject_id})
            flash("Subject updated.", 'success')
        except Exception as e: flash(f"Error: {e}", 'error')
    return redirect(url_for('manage_subjects'))

@app.route('/admin/subject/delete/<subject_id>', methods=['POST'])
@admin_required
def delete_subject(subject_id): # subject_id is the name
    db = get_db()
    try:
        # Cascade delete lectures
        lectures = db.Table('Lectures').scan(FilterExpression=Attr('subject_name').eq(subject_id)).get('Items', [])
        for lect in lectures:
             delete_lecture_fully(lect['lecture_id'])
        
        db.Table('Subjects').delete_item(Key={'name': subject_id})
        flash("Subject and its lectures deleted.", 'success')
    except Exception as e: flash(f"Error: {e}", 'error')
    return redirect(url_for('manage_subjects'))

@app.route('/admin/lecture/delete/<lecture_id>', methods=['POST'])
@admin_required
def delete_lecture(lecture_id):
    try:
        delete_lecture_fully(lecture_id)
        flash("Lecture deleted.", 'success')
    except Exception as e: flash(f"Error: {e}", 'error')
    return redirect(url_for('manage_subjects'))

@app.route('/admin/users')
@admin_required
def manage_users():
    db = get_db()
    users = db.Table('Users').scan().get('Items', [])
    users.sort(key=lambda x: x['username'])
    return render_template('manage_users.html', users=users)

@app.route('/admin/user/add', methods=['POST'])
@admin_required
def add_user():
    username = request.form.get('username')
    password = request.form.get('password')
    if username and password:
        hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
        try:
            db = get_db()
            table = db.Table('Users')
            if 'Item' in table.get_item(Key={'username': username}):
                 flash("Username taken.", 'error')
            else:
                 # Calculate next numeric ID
                 all_users = table.scan().get('Items', [])
                 max_id = 0
                 for u in all_users:
                     try:
                         uid = int(u.get('user_id', 0))
                         if uid > max_id: max_id = uid
                     except ValueError:
                         pass # Ignore non-numeric IDs (like uuids or 'admin')
                 
                 new_id = str(int(max_id) + 1)

                 table.put_item(Item={
                     'username': username, 
                     'user_id': new_id, 
                     'password_hash': hashed, 
                     'role': 'student',
                     'created_at': str(time.time())
                 })
                 flash(f"User '{username}' added.", 'success')
        except Exception as e: flash(f"Error: {e}", 'error')
    return redirect(url_for('manage_users'))

@app.route('/admin/user/edit/<user_id>', methods=['GET', 'POST'])
@admin_required
def edit_user(user_id):
    db = get_db()
    table = db.Table('Users')
    
    if request.method == 'POST':
        password = request.form.get('password')
        role = request.form.get('role')
        
        # CRITICAL: Validate role is not None or empty
        if not role or role.strip() == '':
            flash('Error: Role is required and cannot be empty.', 'error')
            return redirect(request.url)
        
        # CRITICAL: Validate role is either 'student' or 'admin'
        if role not in ['student', 'admin']:
            flash('Error: Invalid role. Must be either "student" or "admin".', 'error')
            return redirect(request.url)
        
        try:
            update_expr = "set #r=:r"
            expr_attr_names = {'#r': 'role'}
            expr_attr_values = {':r': role}
            
            if password:
                hashed = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')
                update_expr += ", password_hash=:p"
                expr_attr_values[':p'] = hashed
            
            table.update_item(
                Key={'username': user_id},
                UpdateExpression=update_expr,
                ExpressionAttributeNames=expr_attr_names,
                ExpressionAttributeValues=expr_attr_values
            )
            flash(f"User '{user_id}' updated.", 'success')
            return redirect(url_for('manage_users'))
        except Exception as e:
            flash(f"Error updating user: {e}", 'error')
            
    try:
        response = table.get_item(Key={'username': user_id})
        user = response.get('Item')
        if not user:
            flash("User not found.", 'error')
            return redirect(url_for('manage_users'))
        return render_template('edit_user.html', user=user)
    except Exception as e:
        flash(f"Error fetching user: {e}", 'error')
        return redirect(url_for('manage_users'))

@app.route('/admin/user/delete/<user_id>', methods=['POST'])
@admin_required
def delete_user(user_id):
    if user_id == 'admin':
        flash("Cannot delete the main admin user.", 'error')
        return redirect(url_for('manage_users'))
        
    try:
        db = get_db()
        db.Table('Users').delete_item(Key={'username': user_id})
        flash(f"User '{user_id}' deleted.", 'success')
    except Exception as e:
        flash(f"Error deleting user: {e}", 'error')
    return redirect(url_for('manage_users'))

@app.route('/admin/upload', methods=['GET', 'POST'])
@admin_required
def upload_page():
    db = get_db()
    subjects = [r['name'] for r in db.Table('Subjects').scan().get('Items', [])]
    subjects.sort()

    if request.method == 'POST':
        title = request.form.get('title')
        sub_name = request.form.get('subject')
        file = request.files.get('file')

        if not title or not sub_name or not file or file.filename == '':
            flash("All fields are required.", 'error')
            return redirect(request.url)

        if allowed_file(file.filename):
            fname = secure_filename(file.filename)
            path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
            file.save(path)
            try:
                process_and_store_lecture(path, title, sub_name)
                flash(f"Processed '{fname}' successfully.", 'success')
            except Exception as e:
                flash(f"Upload Failed: {e}", 'error')
                if os.path.exists(path): os.remove(path)
            return redirect(url_for('upload_page'))
        else:
            flash('Invalid file type.', 'error')
    
    return render_template('upload.html', subjects=subjects)

# =========================================
# STUDENT ROUTES
# =========================================
@app.route('/app')
@login_required
def summarizer_dashboard():
    db = get_db()
    data = []
    try:
        subjects = db.Table('Subjects').scan().get('Items', [])
        all_lectures = db.Table('Lectures').scan().get('Items', [])
        
        for sub in subjects:
            lectures = [l for l in all_lectures if l.get('subject_name') == sub['name']]
            data.append({'id': sub['name'], 'name': sub['name'], 'lectures': lectures})
    except: data = []
    return render_template('summarizer_app.html', subjects_data=data)

@app.route('/app/check_size/<lecture_id>')
@login_required
def check_lecture_size(lecture_id):
    db = get_db()
    try:
        # Count chunks using Query (Count)
        response = db.Table('LectureChunks').query(
            KeyConditionExpression=Key('lecture_id').eq(lecture_id),
            Select='COUNT'
        )
        return jsonify({'chunk_count': response['Count']})
    except Exception as e:
        print(f"Error checking lecture size: {e}")
        return jsonify({'chunk_count': 0})

@app.route('/app/generate_summary_ajax/<lecture_id>', methods=['POST'])
@login_required
def generate_summary_ajax(lecture_id):
    try:
        data = request.get_json(silent=True) or {}
        target_words = str(data.get('target_words', 600)) # Ensure string for Dynamo SK
        force_refresh = data.get('force_refresh', False)
        
        db = get_db()
        
        # 1. Check Cache
        cached = None
        if not force_refresh:
            resp = db.Table('Summaries').get_item(Key={'lecture_id': lecture_id, 'summary_type': target_words})
            if 'Item' in resp:
                print(f"--- [CACHE HIT] {lecture_id} ---")
                return jsonify({'success': True, 'summary': resp['Item']['content']})
        
        # 2. Generate
        chunks = retrieve_chunks_for_lecture(lecture_id)
        if not chunks: return jsonify({'success': False, 'error': 'No content.'}), 404
        
        final_summary = run_single_shot_summary(chunks, int(target_words))
        if "Error:" in final_summary: return jsonify({'success': False, 'error': final_summary}), 500
        
        # 3. Save Cache
        db.Table('Summaries').put_item(Item={
            'lecture_id': lecture_id,
            'summary_type': target_words,
            'content': final_summary,
            'created_at': str(time.time())
        })
        
        return jsonify({'success': True, 'summary': final_summary})
    except Exception as e:
        print(f"RAG Error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/app/view_file/<filename>')
@login_required  
def user_view_file(filename):
    try:
        return send_from_directory(app.config['UPLOAD_FOLDER'], filename, as_attachment=False)
    except FileNotFoundError:
        return "File not found.", 404

if __name__ == '__main__':
    # Auto-create tables on launch (for local dev convenience)
    from db_dynamo import create_tables_if_not_exist
    create_tables_if_not_exist()
    app.run(debug=True, port=8000) # Changed port to 8000
