import os
from werkzeug.utils import secure_filename
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime
from werkzeug.security import generate_password_hash, check_password_hash

app = Flask(__name__)
app.secret_key = 'kilgoris_secret_key' # Required for flashing messages

# Configure where to save images
UPLOAD_FOLDER = 'static/uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Helper function to check file types
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Database Connection
app.config['SQLALCHEMY_DATABASE_URI'] = 'mysql+mysqlconnector://root:Leteipa2005@localhost/kilgoris_news'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)

# --- Database Models ---

class User(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    fullname = db.Column(db.String(100), nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    location = db.Column(db.String(100), nullable=False)
    password = db.Column(db.String(200), nullable=False)
    date_joined = db.Column(db.DateTime, default=datetime.utcnow)
    # Relationship for comments
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
    # Relationship for comments
    comments = db.relationship('Comment', backref='article', lazy=True, cascade="all, delete-orphan")

class Comment(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    body = db.Column(db.Text, nullable=False)
    date_posted = db.Column(db.DateTime, default=datetime.utcnow)
    article_id = db.Column(db.Integer, db.ForeignKey('article.id'), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)

# --- Routes ---

@app.route('/')
def home():
    categories = Category.query.all()
    latest_articles = Article.query.order_by(Article.date_posted.desc()).limit(5).all()
    return render_template('index.html', categories=categories, articles=latest_articles)

@app.route('/edit_comment/<int:comment_id>', methods=['POST'])
def edit_comment(comment_id):
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    comment = Comment.query.get_or_404(comment_id)
    
    # Security check: Is this the author of the comment?
    if comment.user_id != session['user_id']:
        return "You do not have permission to edit this comment", 403

    new_body = request.form.get('comment_body')
    if new_body:
        comment.body = new_body
        db.session.commit()
    
    return redirect(url_for('article', article_id=comment.article_id))

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

        # Check if user already exists
        user_exists = User.query.filter_by(email=email).first()
        if user_exists:
            return "Email already registered!"

        # Hash password for security and save user
        hashed_pw = generate_password_hash(password, method='pbkdf2:sha256')
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
        else:
            return "Login Failed. Please check your email and password."
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() 
    return redirect(url_for('home'))

# --- Admin Section (Dashboard, Post, Delete) ---

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

# --- News & Interaction Section (Search, Article, Comments) ---

@app.route('/search')
def search():
    query = request.args.get('q')
    if query:
        results = Article.query.filter(
            (Article.title.like(f'%{query}%')) | 
            (Article.content.like(f'%{query}%'))
        ).all()
    else:
        results = []
    return render_template('index.html', articles=results, search_query=query)

@app.route('/article/<int:article_id>', methods=['GET', 'POST'])
def article(article_id):
    article = Article.query.get_or_404(article_id)
    
    # Handle Comment Submission
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

    # Trending Sidebar: 3 other stories from the same category
    trending = Article.query.filter(
        Article.category_id == article.category_id, 
        Article.id != article_id
    ).limit(3).all()
    
    return render_template('article.html', article=article, trending=trending)

if __name__ == '__main__':
    # Ensure tables are created for the new Comment model
    with app.app_context():
        db.create_all()
    app.run(debug=True)