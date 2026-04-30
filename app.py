import os
import logging
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

# ------------------ APP SETUP ------------------
app = Flask(__name__)

# Logging (helps debug Render errors)
logging.basicConfig(level=logging.DEBUG)

# Secret key
app.secret_key = os.environ.get('SECRET_KEY', 'dev_secret_key')

# ------------------ FILE UPLOAD ------------------
UPLOAD_FOLDER = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'static/uploads')
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ------------------ DATABASE CONFIG ------------------
database_url = os.environ.get('DATABASE_URL')

if not database_url:
    raise ValueError("❌ DATABASE_URL is not set in Render environment variables")

# Fix postgres:// issue
if database_url.startswith("postgres://"):
    database_url = database_url.replace("postgres://", "postgresql://", 1)

app.config['SQLALCHEMY_DATABASE_URI'] = database_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# ------------------ MODELS ------------------
class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)
    comments = db.relationship('Comment', backref='author', lazy=True)

class Category(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(50), nullable=False, unique=True)
    articles = db.relationship('Article', backref='category', lazy=True)

class Article(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    title = db.Column(db.String(200), nullable=False)
    content = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    category_id = db.Column(db.Integer, db.ForeignKey('category.id'), nullable=False)
    image_file = db.Column(db.String(100), default='default.jpg')
    comments = db.relationship('Comment', backref='article', lazy=True, cascade="all, delete-orphan")

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# ------------------ ROUTES ------------------
@app.route('/')
def home():
    categories = Category.query.all()
    latest_articles = Article.query.order_by(Article.date_posted.desc()).limit(5).all()
    return render_template('index.html', categories=categories, articles=latest_articles)

@app.route('/donate')
def donate():
    return render_template('donate.html')

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        fullname = request.form.get('fullname')
        email = request.form.get('email')
        location = request.form.get('location')
        password = request.form.get('password')

        if User.query.filter_by(email=email).first():
            flash("Email already registered!", "danger")
            return redirect(url_for('register'))

        hashed_pw = generate_password_hash(password)
        new_user = User(fullname=fullname, email=email, location=location, password=hashed_pw)

        db.session.add(new_user)
        db.session.commit()

        return redirect(url_for('login'))

    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email')
        password = request.form.get('password')

        user = User.query.filter_by(email=email).first()

        if user and check_password_hash(user.password, password):
            session['user_id'] = user.id
            session['user_name'] = user.fullname
            return redirect(url_for('home'))

        flash("Invalid email or password", "danger")

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

@app.route('/admin/dashboard')
def admin_dashboard():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    articles = Article.query.order_by(Article.date_posted.desc()).all()
    return render_template('admin_dashboard.html', articles=articles)

@app.route('/admin/post', methods=['GET', 'POST'])
def create_article():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        title = request.form.get('title')
        content = request.form.get('content')
        category_id = request.form.get('category_id')

        file = request.files.get('image')
        filename = 'default.jpg'

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))

        new_article = Article(
            title=title,
            content=content,
            category_id=category_id,
            image_file=filename
        )

        db.session.add(new_article)
        db.session.commit()

        return redirect(url_for('admin_dashboard'))

    categories = Category.query.all()
    return render_template('create_article.html', categories=categories)

@app.route('/admin/delete/<int:article_id>')
def delete_article(article_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))

    article = Article.query.get_or_404(article_id)

    db.session.delete(article)
    db.session.commit()

    return redirect(url_for('admin_dashboard'))

@app.route('/article/<int:article_id>', methods=['GET', 'POST'])
def article(article_id):
    article = Article.query.get_or_404(article_id)

    if request.method == 'POST':
        if 'user_id' not in session:
            return redirect(url_for('login'))

        comment_body = request.form.get('comment_body')

        if comment_body:
            new_comment = Comment(
                body=comment_body,
                article_id=article.id,
                user_id=session['user_id']
            )

            db.session.add(new_comment)
            db.session.commit()

            return redirect(url_for('article', article_id=article.id))

    trending = Article.query.filter(
        Article.category_id == article.category_id,
        Article.id != article_id
    ).limit(3).all()

    return render_template('article.html', article=article, trending=trending)

# ------------------ SAFE DB INIT ------------------
@app.before_first_request
def initialize_database():
    try:
        db.create_all()
        if not Category.query.first():
            db.session.add(Category(name="General News"))
            db.session.commit()
            print("✅ Database initialized")
    except Exception as e:
        print("❌ DB INIT ERROR:", e)

# ------------------ RUN ------------------
if __name__ == '__main__':
    port = int(os.environ.get("PORT", 5000))
    app.run(host='0.0.0.0', port=port)