# ⚡ Must be the very first lines – enable gevent async support
from gevent import monkey
monkey.patch_all()

import os
import random
import string
import logging
from datetime import datetime, timedelta
import cloudinary
import cloudinary.uploader
from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    session, abort, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer
from authlib.integrations.flask_client import OAuth
from flask_socketio import SocketIO, emit
from functools import wraps

# --- APP INITIALIZATION ---
app = Flask(__name__)

# Secret key – MUST be set in environment, no fallback
app.config['SECRET_KEY'] = os.environ.get('SECRET_KEY')
if not app.config['SECRET_KEY']:
    raise RuntimeError("SECRET_KEY environment variable is not set.")
app.secret_key = app.config['SECRET_KEY']

# Logging
logging.basicConfig(level=logging.INFO)
app.logger.setLevel(logging.INFO)

# Socket.IO
socketio = SocketIO(
    app,
    cors_allowed_origins="*",
    ping_timeout=60,
    ping_interval=25
)

# OAuth
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

# Serializer for reset tokens
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# --- CLOUDINARY CONFIGURATION ---
cloudinary.config(
    cloud_name=os.environ.get('CLOUDINARY_CLOUD_NAME'),
    api_key=os.environ.get('CLOUDINARY_API_KEY'),
    api_secret=os.environ.get('CLOUDINARY_API_SECRET')
)
if not all([cloudinary.config().cloud_name, cloudinary.config().api_key, cloudinary.config().api_secret]):
    raise RuntimeError("Cloudinary environment variables must be set.")

# --- APP CONFIG ---
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024  # 50MB

# Email config
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.environ.get('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.environ.get('MAIL_PASSWORD')
mail = Mail(app)

# Database
database_url = os.environ.get('DATABASE_URL')
if database_url and database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)
app.config['SQLALCHEMY_DATABASE_URI'] = database_url or 'sqlite:///kilgoris.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# --- CSRF PROTECTION ---
def generate_csrf_token():
    if '_csrf_token' not in session:
        import secrets
        session['_csrf_token'] = secrets.token_hex(32)
    return session['_csrf_token']

app.jinja_env.globals['csrf_token'] = generate_csrf_token

def csrf_protect(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if request.method in ('POST', 'PUT', 'DELETE'):
            token = session.get('_csrf_token', None)
            form_token = request.form.get('_csrf_token')
            if not token or not form_token or token != form_token:
                app.logger.warning("CSRF validation failed")
                abort(400, description="CSRF token missing or incorrect.")
        return f(*args, **kwargs)
    return decorated_function

# --- LOGIN REQUIRED DECORATOR ---
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            flash("Please log in or register to access this page.", "info")
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# --- MODELS ---
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    location = db.Column(db.String(100))
    password = db.Column(db.String(200), nullable=False)
    is_admin = db.Column(db.Boolean, default=False)
    is_verified = db.Column(db.Boolean, default=False)
    otp_code = db.Column(db.String(6))
    otp_expiry = db.Column(db.DateTime)
    reputation = db.Column(db.Integer, default=0)
    report_score = db.Column(db.Integer, default=0)
    comments = db.relationship('Comment', backref='author', lazy=True)
    likes = db.relationship('Like', backref='user', lazy=True)
    bookmarks = db.relationship('Bookmark', backref='user', lazy=True)
    topic_follows = db.relationship('TopicFollow', backref='user', lazy=True)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    file_path = db.Column(db.String(500), default='https://via.placeholder.com/800x400')
    is_video = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(50))
    comments = db.relationship('Comment', backref='article', lazy=True, cascade="all, delete-orphan")
    likes = db.relationship('Like', backref='article', lazy=True, cascade="all, delete-orphan")
    bookmarks = db.relationship('Bookmark', backref='article', lazy=True, cascade="all, delete-orphan")

class CommunityReport(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    reporter_name = db.Column(db.String(100), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    category = db.Column(db.String(50), nullable=False)
    title = db.Column(db.String(100), nullable=False)
    location = db.Column(db.String(100), nullable=False)
    description = db.Column(db.Text, nullable=False)
    file_path = db.Column(db.String(300), nullable=True)
    is_approved = db.Column(db.Boolean, default=False)
    date_submitted = db.Column(db.DateTime, default=datetime.utcnow)

class Notification(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    message = db.Column(db.String(255), nullable=False)
    link = db.Column(db.String(255))
    is_read = db.Column(db.Boolean, default=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    user = db.relationship('User', backref=db.backref('notifications', lazy=True))

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False, index=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'))
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy=True)
    likes = db.relationship('Like', backref='comment', lazy=True, cascade="all, delete-orphan")

    @property
    def likes_count(self):
        return len(self.likes)

class Like(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    comment_id = db.Column(db.Integer, db.ForeignKey('comment.id'), nullable=True)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=True)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class Bookmark(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class TopicFollow(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    topic = db.Column(db.String(50), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

# --- REPUTATION CONSTANTS ---
REP_VERIFIED_EMAIL = 5
REP_COMMENT_POSTED = 1
REP_COMMENT_LIKED = 2
REP_REPORT_APPROVED = 10

# --- SOCKET.IO EVENTS ---
@socketio.on('connect')
def handle_connect():
    app.logger.info(f"Client connected: {request.sid}")

@socketio.on('disconnect')
def handle_disconnect():
    app.logger.info(f"Client disconnected: {request.sid}")

@socketio.on('join_user_room')
def handle_join_user_room():
    user_id = session.get('user_id')
    if user_id:
        room = f"user_{user_id}"
        socketio.server.enter_room(request.sid, room, namespace='/')
        app.logger.info(f"User {user_id} joined room {room}")

@socketio.on('join_article_room')
def handle_join_article_room(data):
    article_id = data.get('article_id')
    if article_id:
        room = f"article_{article_id}"
        socketio.server.enter_room(request.sid, room, namespace='/')
        app.logger.info(f"Client joined article room {room}")

@socketio.on('join_community_room')
def handle_join_community_room():
    socketio.server.enter_room(request.sid, 'community_reports', namespace='/')
    app.logger.info("Client joined community_reports room")

# --- ROUTES ---
@app.route('/')
@login_required
def home():
    articles = Article.query.order_by(Article.date_posted.desc()).all()
    return render_template('index.html', articles=articles)

# --- AUTH (public) ---
@app.route('/login/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/login/google/callback')
def google_callback():
    token = google.authorize_access_token()
    user_info = token['userinfo']
    email = user_info['email']
    name = user_info['name']
    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(
            fullname=name,
            email=email,
            password='google_auth',
            is_verified=True,
            reputation=REP_VERIFIED_EMAIL
        )
        db.session.add(user)
        db.session.commit()
    session['user_id'] = user.id
    session['user_name'] = user.fullname
    session['is_admin'] = user.is_admin
    return redirect(url_for('home'))

@app.context_processor
def inject_notifications():
    if 'user_id' in session:
        unread_count = Notification.query.filter_by(
            user_id=session['user_id'], is_read=False
        ).count()
        return dict(unread_count=unread_count)
    return dict(unread_count=0)

@app.context_processor
def inject_latest_articles():
    latest_articles = Article.query.order_by(Article.date_posted.desc()).limit(5).all()
    return dict(latest_articles=latest_articles)

@app.route('/notifications')
@login_required
def notifications():
    user_notifications = Notification.query.filter_by(user_id=session['user_id'])\
        .order_by(Notification.timestamp.desc()).all()
    unread = Notification.query.filter_by(user_id=session['user_id'], is_read=False).all()
    for n in unread:
        n.is_read = True
    db.session.commit()
    return render_template('notifications.html', user_notifications=user_notifications)

# --- ADMIN (protected) ---
@app.route('/admin/approve-report/<int:report_id>')
@login_required
def approve_report(report_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    report = CommunityReport.query.get_or_404(report_id)
    report.is_approved = True

    if report.user_id:
        reporter = User.query.get(report.user_id)
        if reporter:
            reporter.report_score += 1
            reporter.reputation += REP_REPORT_APPROVED
            db.session.commit()
            notif = Notification(
                user_id=report.user_id,
                message=f"Your report '{report.title}' has been approved! +{REP_REPORT_APPROVED} rep",
                link=url_for('community_reporter'),
                is_read=False
            )
            db.session.add(notif)
            db.session.commit()
            unread_count = Notification.query.filter_by(
                user_id=report.user_id, is_read=False
            ).count()
            room = f"user_{report.user_id}"
            socketio.emit('notification_update', {
                'message': notif.message,
                'link': notif.link,
                'unread_count': unread_count
            }, room=room)
    else:
        db.session.commit()

    socketio.emit('report_approved', {
        'id': report.id,
        'reporter_name': report.reporter_name,
        'category': report.category,
        'title': report.title,
        'location': report.location,
        'description': report.description,
        'file_path': report.file_path,
        'date_submitted': report.date_submitted.strftime('%Y-%m-%d %H:%M')
    }, room='community_reports')

    flash("Report approved and is now live!", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/delete-report/<int:report_id>')
@login_required
def delete_report(report_id):
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    report = CommunityReport.query.get_or_404(report_id)
    db.session.delete(report)
    db.session.commit()
    flash("Report deleted.", "info")
    return redirect(url_for('admin_dashboard'))

# --- COMMUNITY REPORTER ---
@app.route('/community-reporter', methods=['GET', 'POST'])
@login_required
@csrf_protect
def community_reporter():
    if request.method == 'POST':
        reporter_name = request.form.get('reporter_name')
        category = request.form.get('category')
        title = request.form.get('title')
        location = request.form.get('location')
        description = request.form.get('description')
        file = request.files.get('report_file')
        user_id = session.get('user_id', None)
        file_url = None

        if file and file.filename != '':
            filename = file.filename.lower()
            if filename.endswith(('.mp4', '.mov', '.avi', '.mkv')):
                res_type = "video"
            elif filename.endswith(('.jpg', '.jpeg', '.png', '.gif', '.webp')):
                res_type = "image"
            else:
                res_type = "raw"
            try:
                upload_result = cloudinary.uploader.upload(file, resource_type=res_type)
                file_url = upload_result.get('secure_url')
            except Exception as e:
                app.logger.error(f"Cloudinary upload failed: {e}")
                flash(f"File upload failed: {e}", "danger")
                return redirect(url_for('community_reporter'))

        new_report = CommunityReport(
            reporter_name=reporter_name,
            user_id=user_id,
            category=category,
            title=title,
            location=location,
            description=description,
            file_path=file_url
        )
        db.session.add(new_report)
        db.session.commit()
        flash("Report submitted successfully! It will appear after admin approval.", "success")
        return redirect(url_for('community_reporter'))

    reports = CommunityReport.query.filter_by(is_approved=True)\
        .order_by(CommunityReport.date_submitted.desc()).all()
    return render_template('community_reporter.html', reports=reports)

# --- REGISTRATION (public) ---
@app.route('/register', methods=['GET', 'POST'])
@csrf_protect
def register():
    if request.method == 'POST':
        email = request.form.get('email')
        if User.query.filter_by(email=email).first():
            flash("Email already registered", "danger")
            return redirect(url_for('register'))

        otp = ''.join(random.choices(string.digits, k=6))
        otp_expiry = datetime.utcnow() + timedelta(minutes=10)
        hashed_pw = generate_password_hash(request.form.get('password'))
        new_user = User(
            fullname=request.form.get('fullname'),
            email=email,
            password=hashed_pw,
            otp_code=otp,
            otp_expiry=otp_expiry
        )
        db.session.add(new_user)
        db.session.commit()

        try:
            msg = Message(
                'Verify your Kilgoris News Account',
                sender=app.config['MAIL_USERNAME'],
                recipients=[email]
            )
            msg.body = f"Your verification code is: {otp} (expires in 10 minutes)"
            mail.send(msg)
            session['verify_email'] = email
            return redirect(url_for('verify'))
        except Exception as e:
            app.logger.error(f"Failed to send verification email: {e}")
            flash("Account created, but verification email could not be sent. "
                  "Please request a new code on the verification page.", "warning")
            session['verify_email'] = email
            return redirect(url_for('verify'))

    return render_template('register.html')

@app.route('/verify', methods=['GET', 'POST'])
@csrf_protect
def verify():
    if request.method == 'POST':
        user = User.query.filter_by(email=session.get('verify_email')).first()
        if not user:
            flash("No account found. Please register again.", "danger")
            return redirect(url_for('register'))
        if user.otp_expiry and datetime.utcnow() > user.otp_expiry:
            flash("Verification code has expired. Request a new one below.", "warning")
            return render_template('verify.html', resend=True)
        if user.otp_code == request.form.get('otp'):
            user.is_verified = True
            user.reputation += REP_VERIFIED_EMAIL
            user.otp_code = None
            user.otp_expiry = None
            db.session.commit()
            session.pop('verify_email', None)
            flash("Email verified! You can now login.", "success")
            return redirect(url_for('login'))
        flash("Invalid code", "danger")
    return render_template('verify.html', resend=False)

@app.route('/resend_otp', methods=['POST'])
@csrf_protect
def resend_otp():
    email = session.get('verify_email')
    if not email:
        flash("Session expired. Please register again.", "danger")
        return redirect(url_for('register'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash("User not found.", "danger")
        return redirect(url_for('register'))
    new_otp = ''.join(random.choices(string.digits, k=6))
    user.otp_code = new_otp
    user.otp_expiry = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    try:
        msg = Message(
            'New Verification Code - Kilgoris News',
            sender=app.config['MAIL_USERNAME'],
            recipients=[email]
        )
        msg.body = f"Your new verification code is: {new_otp} (valid for 10 minutes)"
        mail.send(msg)
        flash("A new code has been sent to your email.", "info")
    except Exception as e:
        app.logger.error(f"Failed to resend OTP: {e}")
        flash("Could not send email. Please try again later.", "danger")
    return redirect(url_for('verify'))

@app.route('/forgot_password', methods=['GET', 'POST'])
@csrf_protect
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email')
        user = User.query.filter_by(email=email).first()
        if user:
            token = s.dumps(email, salt='password-reset-salt')
            link = url_for('reset_password', token=token, _external=True)
            msg = Message(
                'Password Reset Request - Kilgoris News',
                sender=app.config['MAIL_USERNAME'],
                recipients=[email]
            )
            msg.body = f'To reset your password, visit: {link}'
            try:
                mail.send(msg)
                flash('Reset link sent to your email.', 'info')
            except Exception as e:
                app.logger.error(f"Failed to send reset email: {e}")
                flash("Could not send email. Please try again later.", "danger")
            return redirect(url_for('login'))
        flash('Email not found.', 'danger')
    return render_template('forgot_password.html')

@app.route('/reset_password/<token>', methods=['GET', 'POST'])
@csrf_protect
def reset_password(token):
    try:
        email = s.loads(token, salt='password-reset-salt', max_age=1800)
    except Exception:
        flash('Link expired or invalid.', 'danger')
        return redirect(url_for('forgot_password'))
    user = User.query.filter_by(email=email).first()
    if not user:
        flash('User not found. Please register first.', 'danger')
        return redirect(url_for('register'))
    if request.method == 'POST':
        new_password = request.form.get('password')
        user.password = generate_password_hash(new_password)
        db.session.commit()
        flash('Password updated! You can now login.', 'success')
        return redirect(url_for('login'))
    return render_template('reset_token.html', token=token)

# --- ADMIN CREATE ARTICLE ---
@app.route('/admin/post', methods=['GET', 'POST'])
@login_required
@csrf_protect
def create_article():
    if not session.get('is_admin'):
        flash("Unauthorized access", "danger")
        return redirect(url_for('home'))
    if request.method == 'POST':
        file = request.files.get('file')
        file_url = 'https://via.placeholder.com/800x400'
        is_video = False
        if file and file.filename != '':
            file.seek(0)
            if file.filename.lower().endswith(('.mp4', '.mov', '.avi', '.mkv')):
                is_video = True
            try:
                upload_result = cloudinary.uploader.upload(
                    file,
                    resource_type="video" if is_video else "image",
                    transformation=[
                        {'width': 800, 'height': 500, 'crop': 'fill', 'gravity': 'auto'}
                    ] if not is_video else []
                )
                file_url = upload_result.get('secure_url')
            except Exception as e:
                app.logger.error(f"Cloudinary upload error: {e}")
                flash(f"Upload failed: {e}", "danger")
                return redirect(url_for('create_article'))

        new_art = Article(
            title=request.form.get('title'),
            content=request.form.get('content'),
            category=request.form.get('category'),
            file_path=file_url,
            is_video=is_video
        )
        db.session.add(new_art)
        db.session.commit()

        data = {
            "id": new_art.id,
            "title": new_art.title,
            "category": new_art.category or 'Latest Update',
            "image": new_art.file_path,
            "is_video": new_art.is_video,
            "url": url_for('article', article_id=new_art.id),
            "excerpt": new_art.content[:100] + '...'
        }
        socketio.emit('new_article', data)
        flash("Article Published Successfully!", "success")
        return redirect(url_for('home'))
    return render_template('create_article.html')

# --- LOGIN / LOGOUT (public) ---
@app.route('/login', methods=['GET', 'POST'])
@csrf_protect
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            if not user.is_verified:
                flash("Please verify your email before logging in.", "warning")
                session['verify_email'] = user.email
                return redirect(url_for('verify'))
            session['user_id'] = user.id
            session['user_name'] = user.fullname
            session['is_admin'] = user.is_admin
            return redirect(url_for('home'))
        flash("Invalid email or password.", "danger")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

# --- MISC PAGES (some may be public) ---
@app.route('/support')
def support():
    return render_template('support.html')

@app.route('/search')
@login_required
def search():
    query = request.args.get('q')
    if query:
        results = Article.query.filter(
            (Article.title.contains(query)) | (Article.content.contains(query))
        ).order_by(Article.date_posted.desc()).all()
    else:
        results = []
    return render_template('index.html', articles=results,
                           category_title=f"SEARCH RESULTS FOR: {query}")

@app.route('/category/<string:cat_name>')
@login_required
def category(cat_name):
    category_articles = Article.query.filter_by(category=cat_name)\
        .order_by(Article.date_posted.desc()).all()
    return render_template('index.html', articles=category_articles,
                           category_title=cat_name.upper())

@app.route('/admin')
@login_required
def admin_dashboard():
    if not session.get('is_admin'):
        return redirect(url_for('login'))
    articles = Article.query.order_by(Article.date_posted.desc()).all()
    reports = CommunityReport.query.order_by(CommunityReport.date_submitted.desc()).all()
    return render_template('admin_dashboard.html', articles=articles, reports=reports)

@app.route('/admin/delete/<int:article_id>')
@login_required
def delete_article(article_id):
    if not session.get('is_admin'):
        return redirect(url_for('home'))
    art = Article.query.get_or_404(article_id)
    db.session.delete(art)
    db.session.commit()
    flash("Article deleted", "info")
    return redirect(url_for('admin_dashboard'))

@app.route('/donate')
def donate():
    return render_template('donate.html')

# --- ARTICLE (with comment + reputation + real-time) ---
@app.route('/article/<int:article_id>', methods=['GET', 'POST'])
@login_required
@csrf_protect
def article(article_id):
    art = Article.query.get_or_404(article_id)
    if request.method == 'POST':
        body = request.form.get('body')
        parent_id = request.form.get('parent_id')
        comment = Comment(
            body=body,
            article_id=article_id,
            user_id=session['user_id'],
            parent_id=parent_id
        )
        db.session.add(comment)
        db.session.commit()
        # Reputation for comment author
        comment.author.reputation += REP_COMMENT_POSTED
        db.session.commit()

        commenter_name = User.query.get(session['user_id']).fullname
        socketio.emit('new_comment', {
            'id': comment.id,
            'body': comment.body,
            'author': commenter_name,
            'parent_id': parent_id or 0,
            'date_posted': comment.date_posted.strftime('%b %d, %H:%M'),
            'article_id': article_id
        }, room=f"article_{article_id}")

        flash("Comment posted.", "success")
        return redirect(url_for('article', article_id=article_id))
    return render_template('article.html', article=art)

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy.html')

# --- 🆕 COMMUNITY ENGAGEMENT ROUTES ---

# Real Likes (AJAX)
@app.route('/like/<string:obj_type>/<int:obj_id>', methods=['POST'])
@login_required
@csrf_protect
def toggle_like(obj_type, obj_id):
    user_id = session['user_id']
    liker = User.query.get(user_id)

    if obj_type == 'comment':
        comment = Comment.query.get_or_404(obj_id)
        existing = Like.query.filter_by(user_id=user_id, comment_id=obj_id).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
            return jsonify({'liked': False, 'count': comment.likes_count})
        else:
            like = Like(user_id=user_id, comment_id=obj_id)
            db.session.add(like)
            if comment.user_id != user_id:
                comment.author.reputation += REP_COMMENT_LIKED
                # Notification for comment owner
                notif = Notification(
                    user_id=comment.user_id,
                    message=f"{liker.fullname} liked your comment!",
                    link=url_for('article', article_id=comment.article_id),
                    is_read=False
                )
                db.session.add(notif)
                db.session.commit()
                unread_count = Notification.query.filter_by(
                    user_id=comment.user_id, is_read=False
                ).count()
                room = f"user_{comment.user_id}"
                socketio.emit('notification_update', {
                    'message': notif.message,
                    'link': notif.link,
                    'unread_count': unread_count
                }, room=room)
            else:
                db.session.commit()
            return jsonify({'liked': True, 'count': comment.likes_count})

    elif obj_type == 'article':
        article = Article.query.get_or_404(obj_id)
        existing = Like.query.filter_by(user_id=user_id, article_id=obj_id).first()
        if existing:
            db.session.delete(existing)
            db.session.commit()
            return jsonify({'liked': False, 'count': len(article.likes)})
        else:
            like = Like(user_id=user_id, article_id=obj_id)
            db.session.add(like)
            db.session.commit()
            return jsonify({'liked': True, 'count': len(article.likes)})

    return jsonify({'error': 'Invalid type'}), 400

# Bookmarks
@app.route('/bookmark/<int:article_id>', methods=['POST'])
@login_required
@csrf_protect
def toggle_bookmark(article_id):
    user_id = session['user_id']
    existing = Bookmark.query.filter_by(user_id=user_id, article_id=article_id).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash('Bookmark removed.', 'info')
    else:
        bookmark = Bookmark(user_id=user_id, article_id=article_id)
        db.session.add(bookmark)
        db.session.commit()
        flash('Article bookmarked!', 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/my-bookmarks')
@login_required
def my_bookmarks():
    user = User.query.get(session['user_id'])
    bookmarked_articles = [bm.article for bm in user.bookmarks]
    return render_template('index.html', articles=bookmarked_articles,
                           category_title="YOUR BOOKMARKS")

# Topic Follows
@app.route('/follow/<string:topic>', methods=['POST'])
@login_required
@csrf_protect
def follow_topic(topic):
    user_id = session['user_id']
    existing = TopicFollow.query.filter_by(user_id=user_id, topic=topic).first()
    if existing:
        db.session.delete(existing)
        db.session.commit()
        flash(f'Unfollowed {topic}', 'info')
    else:
        follow = TopicFollow(user_id=user_id, topic=topic)
        db.session.add(follow)
        db.session.commit()
        flash(f'Following {topic}!', 'success')
    return redirect(request.referrer or url_for('home'))

@app.route('/my-topics')
@login_required
def my_topics():
    user = User.query.get(session['user_id'])
    followed_topics = [tf.topic for tf in user.topic_follows]
    if not followed_topics:
        return render_template('index.html', articles=[], category_title="YOU FOLLOW NO TOPICS YET")
    articles = Article.query.filter(Article.category.in_(followed_topics))\
        .order_by(Article.date_posted.desc()).all()
    return render_template('index.html', articles=articles,
                           category_title="YOUR FOLLOWED TOPICS")

# Reporter Ranking
@app.route('/reporters')
@login_required
def reporters():
    top_reporters = User.query.filter(User.report_score > 0)\
        .order_by(User.report_score.desc()).limit(20).all()
    return render_template('reporters.html', reporters=top_reporters)

# --- INIT DB ---
with app.app_context():
    db.create_all()

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)