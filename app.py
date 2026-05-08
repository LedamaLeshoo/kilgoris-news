import os
import random
import string
import cloudinary
import cloudinary.uploader
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory
from flask_sqlalchemy import SQLAlchemy
from flask_mail import Mail, Message
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash
from itsdangerous import URLSafeTimedSerializer

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'kilgoris_news_professional_2026')

# Serializer for password reset links
s = URLSafeTimedSerializer(app.config['SECRET_KEY'])

# --- CLOUDINARY CONFIGURATION ---
cloudinary.config( 
  cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME', 'dfowijvky'), 
  api_key = os.environ.get('CLOUDINARY_API_KEY'), 
  api_secret = os.environ.get('CLOUDINARY_API_SECRET') 
)

# --- CONFIGURATION ---
app.config['MAX_CONTENT_LENGTH'] = 50 * 1024 * 1024

# Email Config
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
    comments = db.relationship('Comment', backref='author', lazy=True)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    file_path = db.Column(db.String(500), default='https://via.placeholder.com/800x400')
    is_video = db.Column(db.Boolean, default=False)
    category = db.Column(db.String(50))
    comments = db.relationship('Comment', backref='article', lazy=True, cascade="all, delete-orphan")

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    parent_id = db.Column(db.Integer, db.ForeignKey('comment.id'))
    replies = db.relationship('Comment', backref=db.backref('parent', remote_side=[id]), lazy=True)

# --- ROUTES ---

@app.route('/')
def home():
    articles = Article.query.order_by(Article.date_posted.desc()).all()
    return render_template('index.html', articles=articles)

@app.route('/category/<cat_name>')
def category(cat_name):
    category_articles = Article.query.filter_by(category=cat_name).order_by(Article.date_posted.desc()).all()
    return render_template('index.html', articles=category_articles, category_title=cat_name.upper())

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['is_admin'] = user.is_admin
            return redirect(url_for('home'))
        flash("Invalid credentials", "danger")
    return render_template('login.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        password = request.form.get('password')
        hashed_pw = generate_password_hash(password)
        
        if User.query.filter_by(email=email).first():
            flash("Email already exists", "danger")
            return redirect(url_for('register'))
            
        new_user = User(fullname=fullname, email=email, password=hashed_pw)
        db.session.add(new_user)
        db.session.commit()
        session['user_id'] = new_user.id
        return redirect(url_for('home'))
    return render_template('register.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/admin')
def admin_dashboard():
    if not session.get('is_admin'): return redirect(url_for('login'))
    articles = Article.query.order_by(Article.date_posted.desc()).all()
    return render_template('admin_dashboard.html', articles=articles)

@app.route('/admin/post', methods=['GET', 'POST'])
def admin_post():
    if not session.get('is_admin'): return redirect(url_for('login'))
    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        category = request.form.get('category')
        file = request.files.get('file')

        if not file:
            flash("Please upload an image or video", "danger")
            return redirect(url_for('admin_post'))

        upload_result = cloudinary.uploader.upload(file, resource_type="auto")
        file_url = upload_result['secure_url']
        is_vid = upload_result['resource_type'] == 'video'

        new_art = Article(
            title=title, 
            content=content, 
            category=category, 
            file_path=file_url, 
            is_video=is_vid
        )
        db.session.add(new_art)
        db.session.commit()
        flash("Article published!", "success")
        return redirect(url_for('admin_dashboard'))
    return render_template('create_article.html')

@app.route('/admin/delete/<int:article_id>')
def delete_article(article_id):
    if not session.get('is_admin'): return redirect(url_for('home'))
    art = Article.query.get_or_404(article_id)
    db.session.delete(art)
    db.session.commit()
    flash("Article deleted", "info")
    return redirect(url_for('admin_dashboard'))

@app.route('/article/<int:article_id>', methods=['GET', 'POST'])
def article(article_id):
    # SECURITY: Require login to view full article
    if not session.get('user_id'):
        flash("Please login to read the full article", "info")
        return redirect(url_for('login'))
        
    art = Article.query.get_or_404(article_id)
    if request.method == 'POST':
        comment = Comment(
            body=request.form.get('body'), 
            article_id=article_id, 
            user_id=session['user_id'], 
            parent_id=request.form.get('parent_id')
        )
        db.session.add(comment)
        db.session.commit()
        return redirect(url_for('article', article_id=article_id))
    return render_template('article.html', article=art)

@app.route('/donate')
def donate():
    return render_template('donate.html')

@app.route('/privacy-policy')
def privacy_policy():
    return render_template('privacy.html')

if __name__ == '__main__':
    with app.app_context():
        db.create_all()
    app.run(debug=True)