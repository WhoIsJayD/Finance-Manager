import os
import bcrypt
from datetime import datetime, timedelta
import json
from functools import wraps, lru_cache
import uuid
import calendar
import resend
from werkzeug.security import generate_password_hash, check_password_hash
import re
import threading
from queue import Queue
import time
import hashlib
import logging
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from pymongo import MongoClient, ASCENDING, DESCENDING
from pymongo.errors import ConnectionFailure, OperationFailure
from pymongo.server_api import ServerApi
from bson.objectid import ObjectId
import secrets
import shutil
from tenacity import retry, stop_after_attempt, wait_exponential

# Load environment variables
load_dotenv()

# Configure logging
def setup_logging():
    # Create a console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(logging.Formatter(
        '%(asctime)s %(levelname)s: %(message)s [in %(pathname)s:%(lineno)d]'
    ))
    console_handler.setLevel(logging.INFO)

    # Set up app logger
    app_logger = logging.getLogger('app')
    app_logger.setLevel(logging.INFO)
    app_logger.addHandler(console_handler)

    # Set up email logger
    email_logger = logging.getLogger('email')
    email_logger.setLevel(logging.INFO)
    email_logger.addHandler(console_handler)

    return app_logger, email_logger

# Initialize loggers
app_logger, email_logger = setup_logging()

# Initialize Flask app
app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', secrets.token_hex(32))

# Configure session
app.config['SESSION_COOKIE_SECURE'] = os.environ.get('FLASK_ENV') == 'production'
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(days=7)

# Setup rate limiting
limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    default_limits=["200 per day", "50 per hour"],
    storage_uri="memory://"
)

# MongoDB configuration with connection pooling and retry logic
MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
DB_NAME = os.environ.get('DB_NAME', 'finance_manager')

# MongoDB connection settings
MONGO_SETTINGS = {
    'maxPoolSize': 50,  # Maximum number of connections in the pool
    'minPoolSize': 10,  # Minimum number of connections in the pool
    'maxIdleTimeMS': 30000,  # Maximum time a connection can remain idle
    'waitQueueTimeoutMS': 5000,  # How long to wait for a connection from the pool
    'serverSelectionTimeoutMS': 5000,  # How long to wait for server selection
    'connectTimeoutMS': 5000,  # How long to wait for initial connection
    'socketTimeoutMS': 30000,  # How long to wait for operations
    'retryWrites': True,  # Enable automatic retry of write operations
    'retryReads': True,  # Enable automatic retry of read operations
    'w': 'majority',  # Write concern for better durability
    'readPreference': 'secondaryPreferred'  # Read from secondary nodes when possible
}

# Initialize MongoDB connection with retry logic
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def get_mongodb_client():
    try:
        client = MongoClient(MONGO_URI, **MONGO_SETTINGS)
        # Test connection
        client.server_info()
        app_logger.info("Connected to MongoDB successfully")
        return client
    except ConnectionFailure as e:
        app_logger.error(f"Failed to connect to MongoDB: {e}")
        raise

try:
    client = get_mongodb_client()
    db = client[DB_NAME]
except Exception as e:
    app_logger.error(f"Failed to initialize MongoDB connection: {e}")
    raise

# Database collections
users = db['users']
transactions = db['transactions']
budgets = db['budgets']
settings = db['settings']
email_logs = db['email_logs']

# Create optimized indexes for better query performance
def create_indexes():
    try:
        # Drop existing indexes first to avoid conflicts
        users.drop_indexes()
        transactions.drop_indexes()
        budgets.drop_indexes()
        email_logs.drop_indexes()
        
        # Users collection indexes
        users.create_index([("email", ASCENDING)], unique=True)
        users.create_index([("created_at", DESCENDING)])
        users.create_index([("last_login", DESCENDING)])
        
        # Transactions collection indexes
        transactions.create_index([
            ("user_id", ASCENDING),
            ("date", DESCENDING)
        ])
        transactions.create_index([
            ("user_id", ASCENDING),
            ("type", ASCENDING),
            ("date", DESCENDING)
        ])
        transactions.create_index([
            ("user_id", ASCENDING),
            ("category", ASCENDING),
            ("date", DESCENDING)
        ])
        transactions.create_index([
            ("user_id", ASCENDING),
            ("tags", ASCENDING)
        ])
        transactions.create_index([
            ("user_id", ASCENDING),
            ("payment_method", ASCENDING)
        ])
        
        # Budgets collection indexes
        budgets.create_index([
            ("user_id", ASCENDING),
            ("category", ASCENDING)
        ], unique=True)
        
        # Email logs collection indexes
        email_logs.create_index([("timestamp", DESCENDING)])
        email_logs.create_index([("to", ASCENDING), ("timestamp", DESCENDING)])
        email_logs.create_index([("status", ASCENDING)])
        
        app_logger.info("Database indexes created successfully")
    except OperationFailure as e:
        app_logger.error(f"Error creating indexes: {e}")
        # Don't raise the error, just log it
        # This allows the application to continue running even if index creation fails
        return False
    except Exception as e:
        app_logger.error(f"Unexpected error creating indexes: {e}")
        return False
    
    return True

# Create indexes
create_indexes()

# Helper function for MongoDB operations with retry logic
@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=4, max=10))
def execute_mongo_operation(operation, *args, **kwargs):
    try:
        return operation(*args, **kwargs)
    except ConnectionFailure as e:
        app_logger.error(f"MongoDB connection error: {e}")
        raise
    except OperationFailure as e:
        app_logger.error(f"MongoDB operation error: {e}")
        raise

# Example of using the helper function for a query
def get_user_by_email(email):
    return execute_mongo_operation(
        users.find_one,
        {'email': email}
    )

# Example of using the helper function for an insert
def insert_transaction(transaction_data):
    return execute_mongo_operation(
        transactions.insert_one,
        transaction_data
    )

# Example of using the helper function for an update
def update_user_settings(user_id, settings):
    return execute_mongo_operation(
        users.update_one,
        {'_id': ObjectId(user_id)},
        {'$set': {'settings': settings}}
    )

# Example of using the helper function for aggregation
def get_monthly_stats(user_id, start_date, end_date):
    return execute_mongo_operation(
        transactions.aggregate,
        [
            {'$match': {
                'user_id': user_id,
                'date': {'$gte': start_date, '$lt': end_date}
            }},
            {'$group': {
                '_id': '$type',
                'total': {'$sum': '$amount'}
            }}
        ]
    )

# Resend email configuration
RESEND_API_KEY = os.environ.get('RESEND_API_KEY')
DEFAULT_FROM_EMAIL = os.environ.get('DEFAULT_FROM_EMAIL', 'Finance Manager <no-reply@finance-manager.com>')
resend.api_key = RESEND_API_KEY

# Email queue for asynchronous processing
email_queue = Queue(maxsize=1000)

# Background worker for processing emails
def email_worker():
    while True:
        try:
            # Get email task from queue
            task = email_queue.get()
            if task is None:  # Shutdown signal
                break
                
            to_email, subject, html_content, from_email = task
            
            # Process the email directly
            _send_email_direct(to_email, subject, html_content, from_email)
            
            # Mark task as done
            email_queue.task_done()
            
        except Exception as e:
            error_msg = str(e)
            email_logger.error(f"Error processing email: {error_msg}")
            # Don't mark as done if there was an error to prevent queue from emptying
            time.sleep(1)  # Prevent CPU spinning on repeated errors

# Start email worker threads
def start_email_workers(num_workers=2):
    workers = []
    for _ in range(num_workers):
        t = threading.Thread(target=email_worker, daemon=True)
        t.start()
        workers.append(t)
    return workers

# Direct email sending function (without queuing)
def _send_email_direct(to_email, subject, html_content, from_email=DEFAULT_FROM_EMAIL):
    try:
        params = {
            "from": from_email,
            "to": to_email,
            "subject": subject,
            "html": html_content,
        }
        
        response = resend.Emails.send(params)
        
        # Log the email
        email_log = {
            'to': to_email,
            'subject': subject,
            'status': 'success' if response.get('id') else 'failed',
            'response': response,
            'timestamp': datetime.now()
        }
        email_logs.insert_one(email_log)
        
        email_logger.info(f"Email sent to {to_email}: {subject} (ID: {response.get('id')})")
        
        if response.get('id'):
            return True, response.get('id')
        else:
            email_logger.warning(f"Email sending failed without error: {response}")
            return False, "Failed to send email: No ID returned from API"
            
    except Exception as e:
        # Log the error
        error_msg = str(e)
        email_log = {
            'to': to_email,
            'subject': subject,
            'status': 'error',
            'error': error_msg,
            'timestamp': datetime.now()
        }
        email_logs.insert_one(email_log)
        email_logger.error(f"Email error to {to_email}: {error_msg}")
        return False, error_msg

# Enhanced email function with queuing for high load
def send_email(to_email, subject, html_content, from_email=DEFAULT_FROM_EMAIL, queue=True):
    if not RESEND_API_KEY:
        email_logger.warning("Email sending disabled - RESEND_API_KEY not configured")
        return False, "Email sending is disabled (API key not configured)"
        
    if queue:
        try:
            # Add to queue for background processing
            email_queue.put((to_email, subject, html_content, from_email), block=False)
            return True, "Email queued for delivery"
        except Exception as e:
            error_msg = str(e)
            email_logger.error(f"Error queueing email: {error_msg}")
            # Fall back to direct sending if queue is full
            return _send_email_direct(to_email, subject, html_content, from_email)
    else:
        # Direct sending for cases where immediate confirmation is needed
        return _send_email_direct(to_email, subject, html_content, from_email)

# Currency symbols mapping
CURRENCY_SYMBOLS = {
    'USD': '$',
    'EUR': '€',
    'GBP': '£',
    'JPY': '¥',
    'CAD': 'C$',
    'AUD': 'A$',
    'INR': '₹'
}

# Email notification types
EMAIL_NOTIFICATION_TYPES = {
    'weekly_summary': 'Weekly Summary',
    'monthly_report': 'Monthly Report',
    'budget_alerts': 'Budget Alerts',
    'transaction_confirmations': 'Transaction Confirmations',
    'security_alerts': 'Security Alerts'
}

# Context processor for current datetime
@app.context_processor
def inject_now():
    return {'now': datetime.utcnow()}

# Login required decorator
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session:
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

# Admin required decorator
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'user_id' not in session or not is_admin(session['user_id']):
            flash('Access denied: Admin privileges required', 'danger')
            return redirect(url_for('dashboard'))
        return f(*args, **kwargs)
    return decorated_function

# Check if user is admin
def is_admin(user_id):
    user = users.find_one({'_id': ObjectId(user_id)})
    return user and user.get('is_admin', False)

# Simple in-memory caching decorator with time-to-live
def cached(ttl_seconds=300):
    def decorator(func):
        cache = {}
        
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = wrapper._generate_cache_key(*args, **kwargs)
            now = time.time()

            if cache_key in cache:
                result, timestamp = cache[cache_key]
                if now - timestamp < ttl_seconds:
                    return result

            result = func(*args, **kwargs)
            cache[cache_key] = (result, now)

            # Optional: clean up stale entries
            if len(cache) > 1000:
                old_keys = [k for k, (_, ts) in cache.items() if now - ts > ttl_seconds]
                for k in old_keys:
                    del cache[k]

            return result

        # Helper to generate the cache key
        def _generate_cache_key(*args, **kwargs):
            key_parts = [func.__name__]
            key_parts.extend([str(arg) for arg in args])
            key_parts.extend([f"{k}:{v}" for k, v in sorted(kwargs.items())])
            return hashlib.md5(":".join(key_parts).encode()).hexdigest()

        # Invalidate cache manually for specific args/kwargs
        def invalidate(*args, **kwargs):
            key = _generate_cache_key(*args, **kwargs)
            if key in cache:
                del cache[key]

        wrapper._generate_cache_key = _generate_cache_key
        wrapper.invalidate = invalidate
        wrapper.cache = cache
        return wrapper
    return decorator

# Cached version of get_user_settings
@cached(ttl_seconds=300)
def get_user_settings(user_id=None):
    """Get user settings with caching to reduce database load"""
    if not user_id and 'user_id' in session:
        user_id = session['user_id']
    
    try:
        user = users.find_one({'_id': ObjectId(user_id)})
        
        if user and 'settings' in user:
            settings = user['settings']
        else:
            # Default settings if none are found
            settings = {
                'currency': 'USD',
                'date_format': '%Y-%m-%d',
                'theme': 'light',
                'default_categories': {
                    'income': ['Salary', 'Freelance', 'Gifts', 'Investments', 'Other'],
                    'expense': ['Food', 'Housing', 'Transportation', 'Utilities', 'Entertainment', 'Healthcare', 'Education', 'Shopping', 'Personal', 'Other']
                },
                'email_notifications': {
                    'weekly_summary': True,
                    'budget_alerts': True,
                    'security_alerts': True,
                    'monthly_report': False,
                    'transaction_notifications': False
                }
            }
        
        if 'currency_symbols' not in settings:
            settings['currency_symbols'] = CURRENCY_SYMBOLS
        
        return settings
    except Exception as e:
        app_logger.error(f"Error fetching user settings: {e}")
        # Return default settings in case of error
        return {
            'currency': 'USD',
            'date_format': '%Y-%m-%d',
            'theme': 'light',
            'default_categories': {
                'income': ['Salary', 'Other'],
                'expense': ['Food', 'Housing', 'Other']
            },
            'email_notifications': {
                'security_alerts': True
            },
            'currency_symbols': CURRENCY_SYMBOLS
        }

# Get currency symbol based on user's settings
def get_currency_symbol(user_id=None):
    settings = get_user_settings(user_id)
    if settings and 'currency' in settings:
        return CURRENCY_SYMBOLS.get(settings['currency'], '$')
    return '$'

# Template context processor to inject user settings
@app.context_processor
def inject_user_settings():
    if 'user_id' in session:
        try:
            settings = get_user_settings()
            user = users.find_one({'_id': ObjectId(session['user_id'])})
            currency = settings.get('currency', 'USD')
            currency_symbol = settings.get('currency_symbols', {}).get(currency, '$')
            
            # Email notification types for the settings page
            email_notification_types = {
                'weekly_summary': 'Weekly Financial Summary',
                'budget_alerts': 'Budget Threshold Alerts',
                'monthly_report': 'Monthly Financial Report',
                'security_alerts': 'Account Security Alerts',
                'transaction_notifications': 'Large Transaction Notifications'
            }
            
            return {
                'settings': settings,
                'user': user,
                'currency_symbol': currency_symbol,
                'email_notification_types': email_notification_types,
                'is_admin': is_admin(session['user_id']) if user else False,
                'abs': abs  # Make abs function available in templates
            }
        except Exception as e:
            app_logger.error(f"Error in inject_user_settings: {e}")
            return {'settings': {}, 'currency_symbol': '$', 'email_notification_types': {}, 'abs': abs}
    return {'settings': {}, 'currency_symbol': '$', 'email_notification_types': {}, 'abs': abs, 'is_admin': False}

# Handle 404 errors
@app.errorhandler(404)
def page_not_found(e):
    return render_template('404.html'), 404

# Handle 500 errors
@app.errorhandler(500)
def server_error(e):
    app_logger.error(f"Server error: {e}")
    return render_template('500.html'), 500

@app.route('/')
def index():
    if 'user_id' in session:
        return redirect(url_for('dashboard'))
    return render_template('index.html')

@app.route('/register', methods=['GET', 'POST'])
@limiter.limit("10/hour")
def register():
    if request.method == 'POST':
        try:
            email = request.form['email']
            password = request.form['password']
            
            # Validate input
            if not email or not password:
                flash('Email and password are required')
                return redirect(url_for('register'))
                
            if len(password) < 8:
                flash('Password must be at least 8 characters long')
                return redirect(url_for('register'))
            
            # Check if user already exists
            if users.find_one({'email': email}):
                flash('Email already registered')
                return redirect(url_for('register'))
            
            # Hash password
            hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
            
            # Insert user
            user_id = users.insert_one({
                'email': email,
                'password': hashed_password,
                'created_at': datetime.utcnow(),
                'settings': {
                    'currency': 'USD',
                    'date_format': '%Y-%m-%d',
                    'theme': 'light',
                    'default_categories': {
                        'income': ['Salary', 'Freelance', 'Gifts', 'Investments', 'Other'],
                        'expense': ['Food', 'Housing', 'Transportation', 'Utilities', 'Entertainment', 'Healthcare', 'Education', 'Shopping', 'Personal', 'Other']
                    },
                    'email_notifications': {
                        'weekly_summary': True,
                        'budget_alerts': True,
                        'security_alerts': True,
                        'monthly_report': False,
                        'transaction_notifications': False
                    }
                }
            }).inserted_id
            
            session['user_id'] = str(user_id)
            app_logger.info(f"New user registered: {email}")
            
            # Send welcome email
            if RESEND_API_KEY:
                html_content = f"""
                <html>
                <head>
                    <style>
                        body {{ font-family: 'Helvetica', 'Arial', sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
                        h1, h2 {{ color: #3d5c9f; }}
                        .container {{ border: 1px solid #ddd; border-radius: 8px; padding: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
                        .header {{ background-color: #3d5c9f; color: white; padding: 20px; border-radius: 8px 8px 0 0; margin: -20px -20px 20px; text-align: center; }}
                        .footer {{ background-color: #f8f9fa; padding: 15px; border-radius: 0 0 8px 8px; margin: 20px -20px -20px; text-align: center; color: #666; font-size: 0.9em; }}
                        .btn {{ display: inline-block; background-color: #3d5c9f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; margin-top: 15px; }}
                    </style>
                </head>
                <body>
                    <div class="container">
                        <div class="header">
                            <h1 style="margin: 0; padding: 0;">Finance Manager</h1>
                        </div>
                        
                        <h2>Welcome to Finance Manager!</h2>
                        <p>Hi {email},</p>
                        <p>Thank you for registering with Finance Manager. We're excited to help you manage your finances effectively.</p>
                        
                        <p>Here are some things you can do to get started:</p>
                        <ul>
                            <li>Add your income and expenses</li>
                            <li>Set up budgets for different categories</li>
                            <li>Review your financial reports</li>
                            <li>Customize your settings</li>
                        </ul>
                        
                        <p>If you have any questions, please don't hesitate to contact us.</p>
                        
                        <div class="footer">
                            <p>This is an automated message from Finance Manager. Please do not reply to this email.</p>
                            <p>&copy; {datetime.now().year} Finance Manager. All rights reserved.</p>
                        </div>
                    </div>
                </body>
                </html>
                """
                
                send_email(
                    to_email=email,
                    subject="Welcome to Finance Manager",
                    html_content=html_content
                )
            
            return redirect(url_for('dashboard'))
        except Exception as e:
            app_logger.error(f"Registration error: {e}")
            flash('An error occurred during registration. Please try again.')
            return redirect(url_for('register'))
        
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
@limiter.limit("20/hour")
def login():
    if request.method == 'POST':
        try:
            email = request.form['email']
            password = request.form['password']
            
            user = users.find_one({'email': email})
            
            if user and bcrypt.checkpw(password.encode('utf-8'), user['password']):
                session['user_id'] = str(user['_id'])
                session.permanent = True
                
                # Update last login timestamp
                users.update_one(
                    {'_id': user['_id']},
                    {'$set': {'last_login': datetime.utcnow()}}
                )
                
                app_logger.info(f"User logged in: {email}")
                return redirect(url_for('dashboard'))
            else:
                app_logger.warning(f"Failed login attempt for: {email}")
                flash('Invalid credentials')
                
        except Exception as e:
            app_logger.error(f"Login error: {e}")
            flash('An error occurred during login. Please try again.')
            
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

@app.route('/dashboard')
@login_required
def dashboard():
    # Get user settings
    user_settings = get_user_settings()
    
    # Calculate timeframes
    today = datetime.now().date()
    current_month_start = datetime(today.year, today.month, 1)
    
    # Calculate total income, expenses, and balance
    month_income = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'income',
            'date': {'$gte': current_month_start}
        }},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    income = next(month_income, {}).get('total', 0)
    
    month_expense = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'expense',
            'date': {'$gte': current_month_start}
        }},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    expense = next(month_expense, {}).get('total', 0)
    
    balance = income - expense
    
    # Get recent transactions
    recent_transactions = list(transactions.find(
        {'user_id': session['user_id']}
    ).sort('date', -1).limit(5))
    
    # Prepare data for chart
    chart_data = {'labels': [], 'income': [], 'expense': []}
    months = []
    
    # Get data for the last 6 months
    for i in range(5, -1, -1):
        month_date = datetime.now().replace(day=1) - timedelta(days=i*30)
        month_start = datetime(month_date.year, month_date.month, 1)
        month_end = datetime(month_date.year, month_date.month + 1, 1) if month_date.month < 12 else datetime(month_date.year + 1, 1, 1)
        
        month_income = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'type': 'income',
                'date': {'$gte': month_start, '$lt': month_end}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        month_income_total = next(month_income, {}).get('total', 0)
        
        month_expense = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'type': 'expense',
                'date': {'$gte': month_start, '$lt': month_end}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        month_expense_total = next(month_expense, {}).get('total', 0)
        
        chart_data['labels'].append(month_start.strftime('%b %Y'))
        chart_data['income'].append(month_income_total)
        chart_data['expense'].append(month_expense_total)
    
    # Get budgets for dashboard
    user_budgets = list(budgets.find({'user_id': session['user_id']}))
    
    # Calculate spending for each budget
    for budget in user_budgets:
        category_spending = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'category': budget['category'],
                'type': 'expense',
                'date': {'$gte': current_month_start}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        spent = next(category_spending, {}).get('total', 0)
        budget['spent'] = spent
        budget['remaining'] = budget['amount'] - spent
        budget['progress'] = min(100, int((spent / budget['amount']) * 100)) if budget['amount'] > 0 else 100
    
    # Format dates according to user settings
    date_format = user_settings.get('date_format', '%Y-%m-%d')
    for transaction in recent_transactions:
        if 'date' in transaction:
            transaction['formatted_date'] = transaction['date'].strftime(date_format)
    
    return render_template('dashboard.html',
                          income=income,
                          expense=expense,
                          balance=balance,
                          transactions=recent_transactions,
                          chart_data=chart_data,
                          chart_data_json=json.dumps(chart_data),
                          budgets=user_budgets[:4],
                          user_settings=user_settings)

@app.route('/transactions')
@login_required
def view_transactions():
    page = int(request.args.get('page', 1))
    limit = 20
    skip = (page - 1) * limit
    
    # Get user settings
    user_settings = get_user_settings()
    date_format = user_settings.get('date_format', '%Y-%m-%d')
    
    # Get transactions with pagination
    user_transactions = list(transactions.find(
        {'user_id': session['user_id']}
    ).sort('date', -1).skip(skip).limit(limit))
    
    # Format dates according to user settings
    for transaction in user_transactions:
        if 'date' in transaction:
            transaction['formatted_date'] = transaction['date'].strftime(date_format)
    
    # Get total count for pagination
    total = transactions.count_documents({'user_id': session['user_id']})
    
    return render_template('transactions.html',
                           transactions=user_transactions,
                           total=total,
                           page=page,
                           pages=(total // limit) + (1 if total % limit > 0 else 0))

@app.route('/transactions/add', methods=['GET', 'POST'])
@login_required
def add_transaction():
    user_settings = get_user_settings()
    if request.method == 'POST':
        amount = float(request.form['amount'])
        description = request.form['description']
        category = request.form['category']
        type_ = request.form['type']
        date = datetime.strptime(request.form['date'], '%Y-%m-%d')
        tags = request.form.get('tags', '').split(',') if request.form.get('tags') else []
        tags = [tag.strip() for tag in tags if tag.strip()]
        
        transaction_data = {
            'user_id': session['user_id'],
            'amount': amount,
            'description': description,
            'category': category,
            'type': type_,
            'date': date,
            'tags': tags,
            'created_at': datetime.utcnow()
        }
        
        # Add payment method if provided
        if 'payment_method' in request.form and request.form['payment_method']:
            transaction_data['payment_method'] = request.form['payment_method']
            
        # Add notes if provided
        if 'notes' in request.form and request.form['notes']:
            transaction_data['notes'] = request.form['notes']
        
        transactions.insert_one(transaction_data)
        
        flash('Transaction added successfully')
        return redirect(url_for('view_transactions'))
    
    # Get user's categories from settings
    income_categories = user_settings['default_categories']['income']
    expense_categories = user_settings['default_categories']['expense']
    
    return render_template('add_transaction.html', 
                          income_categories=income_categories,
                          expense_categories=expense_categories,
                          income_categories_json=json.dumps(income_categories),
                          expense_categories_json=json.dumps(expense_categories))

@app.route('/transactions/edit/<transaction_id>', methods=['GET', 'POST'])
@login_required
def edit_transaction(transaction_id):
    user_settings = get_user_settings()
    transaction = transactions.find_one({
        '_id': ObjectId(transaction_id),
        'user_id': session['user_id']
    })
    
    if not transaction:
        flash('Transaction not found')
        return redirect(url_for('view_transactions'))
    
    if request.method == 'POST':
        amount = float(request.form['amount'])
        description = request.form['description']
        category = request.form['category']
        type_ = request.form['type']
        date = datetime.strptime(request.form['date'], '%Y-%m-%d')
        tags = request.form.get('tags', '').split(',') if request.form.get('tags') else []
        tags = [tag.strip() for tag in tags if tag.strip()]
        
        update_data = {
            'amount': amount,
            'description': description,
            'category': category,
            'type': type_,
            'date': date,
            'tags': tags,
            'updated_at': datetime.utcnow()
        }
        
        # Add payment method if provided
        if 'payment_method' in request.form and request.form['payment_method']:
            update_data['payment_method'] = request.form['payment_method']
        
        # Add notes if provided
        if 'notes' in request.form and request.form['notes']:
            update_data['notes'] = request.form['notes']
        
        transactions.update_one(
            {'_id': ObjectId(transaction_id)},
            {'$set': update_data}
        )
        
        flash('Transaction updated successfully')
        return redirect(url_for('view_transactions'))
    
    # Get user's categories from settings
    income_categories = user_settings['default_categories']['income']
    expense_categories = user_settings['default_categories']['expense']
    
    # Prepare tags string
    tags_string = ', '.join(transaction.get('tags', []))
    
    return render_template('edit_transaction.html', 
                          transaction=transaction,
                          income_categories=income_categories,
                          expense_categories=expense_categories,
                          income_categories_json=json.dumps(income_categories),
                          expense_categories_json=json.dumps(expense_categories),
                          tags_string=tags_string)

@app.route('/transactions/delete/<transaction_id>', methods=['POST'])
@login_required
def delete_transaction(transaction_id):
    result = transactions.delete_one({
        '_id': ObjectId(transaction_id),
        'user_id': session['user_id']
    })
    
    if result.deleted_count:
        flash('Transaction deleted successfully')
    else:
        flash('Failed to delete transaction')
        
    return redirect(url_for('view_transactions'))

@app.route('/api/transactions', methods=['GET'])
@login_required
def api_transactions():
    start_date = request.args.get('start_date')
    end_date = request.args.get('end_date')
    category = request.args.get('category')
    type_ = request.args.get('type')
    
    query = {'user_id': session['user_id']}
    
    if start_date and end_date:
        start = datetime.strptime(start_date, '%Y-%m-%d')
        end = datetime.strptime(end_date, '%Y-%m-%d')
        query['date'] = {'$gte': start, '$lte': end}
    
    if category:
        query['category'] = category
    
    if type_:
        query['type'] = type_
    
    results = list(transactions.find(query).sort('date', -1))
    
    # Convert ObjectId to string for JSON serialization
    for r in results:
        r['_id'] = str(r['_id'])
        r['date'] = r['date'].strftime('%Y-%m-%d')
    
    return jsonify(results)

@app.route('/reports')
@login_required
def reports():
    # Get categories for filtering
    categories = transactions.distinct('category', {'user_id': session['user_id']})
    
    return render_template('reports.html', categories=categories)

@app.route('/api/reports/category', methods=['GET'])
@login_required
def category_report():
    # Aggregate spending by category
    results = transactions.aggregate([
        {'$match': {'user_id': session['user_id']}},
        {'$group': {
            '_id': '$category',
            'total': {'$sum': '$amount'},
            'count': {'$sum': 1}
        }},
        {'$sort': {'total': -1}}
    ])
    
    report_data = list(results)
    return jsonify(report_data)

@app.route('/api/reports/monthly', methods=['GET'])
@login_required
def monthly_report():
    year = int(request.args.get('year', datetime.utcnow().year))
    
    # Aggregate monthly income and expenses
    results = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'date': {
                '$gte': datetime(year, 1, 1),
                '$lt': datetime(year + 1, 1, 1)
            }
        }},
        {'$project': {
            'month': {'$month': '$date'},
            'amount': 1,
            'type': 1
        }},
        {'$group': {
            '_id': {'month': '$month', 'type': '$type'},
            'total': {'$sum': '$amount'}
        }},
        {'$sort': {'_id.month': 1}}
    ])
    
    report_data = {
        'labels': ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
        'income': [0] * 12,
        'expense': [0] * 12
    }
    
    for data in results:
        month_idx = data['_id']['month'] - 1
        if data['_id']['type'] == 'income':
            report_data['income'][month_idx] = data['total']
        else:
            report_data['expense'][month_idx] = data['total']
    
    return jsonify(report_data)

@app.route('/api/reports/payment-methods', methods=['GET'])
@login_required
def payment_method_report():
    # Get payment method breakdown
    results = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'expense',
            'payment_method': {'$exists': True, '$ne': ''}
        }},
        {'$group': {
            '_id': '$payment_method',
            'total': {'$sum': '$amount'},
            'count': {'$sum': 1}
        }},
        {'$sort': {'total': -1}}
    ])
    
    # Process results
    report_data = list(results)
    
    # Add "Other" category for transactions without payment method
    no_method = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'expense',
            '$or': [
                {'payment_method': {'$exists': False}},
                {'payment_method': ''}
            ]
        }},
        {'$group': {
            '_id': 'Not Specified',
            'total': {'$sum': '$amount'},
            'count': {'$sum': 1}
        }}
    ])
    
    no_method_data = next(no_method, None)
    if no_method_data:
        report_data.append(no_method_data)
    
    return jsonify(report_data)

@app.route('/api/reports/tags', methods=['GET'])
@login_required
def tags_report():
    # Get tags breakdown
    results = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'tags': {'$exists': True, '$ne': []}
        }},
        {'$unwind': '$tags'},
        {'$group': {
            '_id': '$tags',
            'total': {'$sum': '$amount'},
            'count': {'$sum': 1}
        }},
        {'$sort': {'total': -1}},
        {'$limit': 15}
    ])
    
    return jsonify(list(results))

@app.route('/api/reports/trend', methods=['GET'])
@login_required
def trend_report():
    # Time period can be: week, month, quarter, year
    period = request.args.get('period', 'month')
    
    # Number of periods to go back
    num_periods = int(request.args.get('periods', 12))
    
    now = datetime.utcnow()
    
    # Configure time periods
    if period == 'week':
        # Weekly data
        periods = []
        for i in range(num_periods-1, -1, -1):
            end_date = now - timedelta(days=i*7)
            start_date = end_date - timedelta(days=7)
            periods.append({
                'start': start_date,
                'end': end_date,
                'label': f"Week {i+1}"
            })
    elif period == 'quarter':
        # Quarterly data
        periods = []
        current_quarter = (now.month - 1) // 3 + 1
        current_year = now.year
        
        for i in range(num_periods-1, -1, -1):
            q = current_quarter - (i % 4)
            y = current_year - (i // 4) - (1 if q <= 0 else 0)
            if q <= 0:
                q += 4
                
            quarter_start_month = (q - 1) * 3 + 1
            quarter_end_month = q * 3 + 1 if q < 4 else 1
            quarter_end_year = y if q < 4 else y + 1
            
            start_date = datetime(y, quarter_start_month, 1)
            end_date = datetime(quarter_end_year, quarter_end_month, 1)
            
            periods.append({
                'start': start_date,
                'end': end_date,
                'label': f"Q{q} {y}"
            })
    elif period == 'year':
        # Yearly data
        periods = []
        for i in range(num_periods-1, -1, -1):
            year = now.year - i
            start_date = datetime(year, 1, 1)
            end_date = datetime(year+1, 1, 1)
            periods.append({
                'start': start_date,
                'end': end_date,
                'label': str(year)
            })
    else:
        # Default to monthly
        periods = []
        for i in range(num_periods-1, -1, -1):
            month = now.month - i
            year = now.year
            
            while month <= 0:
                month += 12
                year -= 1
                
            next_month = month + 1
            next_year = year
            
            if next_month > 12:
                next_month = 1
                next_year += 1
                
            start_date = datetime(year, month, 1)
            end_date = datetime(next_year, next_month, 1)
            
            periods.append({
                'start': start_date,
                'end': end_date,
                'label': start_date.strftime('%b %Y')
            })
    
    # Collect data for each period
    trend_data = {
        'labels': [],
        'income': [],
        'expense': [],
        'balance': []
    }
    
    for period in periods:
        trend_data['labels'].append(period['label'])
        
        # Get income
        income_result = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'type': 'income',
                'date': {'$gte': period['start'], '$lt': period['end']}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        income = next(income_result, {}).get('total', 0)
        trend_data['income'].append(income)
        
        # Get expense
        expense_result = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'type': 'expense',
                'date': {'$gte': period['start'], '$lt': period['end']}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        expense = next(expense_result, {}).get('total', 0)
        trend_data['expense'].append(expense)
        
        # Calculate balance
        trend_data['balance'].append(income - expense)
    
    return jsonify(trend_data)

@app.route('/api/reports/summary', methods=['GET'])
@login_required
def summary_report():
    # Get overall stats
    
    # All-time totals
    income_total = transactions.aggregate([
        {'$match': {'user_id': session['user_id'], 'type': 'income'}},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    total_income = next(income_total, {}).get('total', 0)
    
    expense_total = transactions.aggregate([
        {'$match': {'user_id': session['user_id'], 'type': 'expense'}},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    total_expense = next(expense_total, {}).get('total', 0)
    
    # Monthly averages
    monthly_avg = transactions.aggregate([
        {'$match': {'user_id': session['user_id']}},
        {'$project': {
            'yearMonth': {'$dateToString': {'format': '%Y-%m', 'date': '$date'}},
            'amount': 1,
            'type': 1
        }},
        {'$group': {
            '_id': {'yearMonth': '$yearMonth', 'type': '$type'},
            'total': {'$sum': '$amount'}
        }},
        {'$group': {
            '_id': '$_id.type',
            'avg': {'$avg': '$total'},
            'count': {'$sum': 1}
        }}
    ])
    
    monthly_averages = {}
    for avg in monthly_avg:
        monthly_averages[avg['_id']] = avg['avg']
    
    # Transaction counts
    transaction_count = transactions.count_documents({'user_id': session['user_id']})
    
    # Category counts
    categories = transactions.aggregate([
        {'$match': {'user_id': session['user_id']}},
        {'$group': {'_id': '$category'}},
        {'$count': 'total'}
    ])
    category_count = next(categories, {}).get('total', 0)
    
    # Recent spending trend (last 7 days vs previous 7 days)
    today = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    last_7_days = today - timedelta(days=7)
    previous_7_days = last_7_days - timedelta(days=7)
    
    recent_spending = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'expense',
            'date': {'$gte': last_7_days}
        }},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    recent_total = next(recent_spending, {}).get('total', 0)
    
    previous_spending = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'type': 'expense',
            'date': {'$gte': previous_7_days, '$lt': last_7_days}
        }},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    previous_total = next(previous_spending, {}).get('total', 0)
    
    spending_change = recent_total - previous_total
    spending_change_percent = (spending_change / previous_total * 100) if previous_total > 0 else 0
    
    # Return summary data
    summary = {
        'totals': {
            'income': total_income,
            'expense': total_expense,
            'balance': total_income - total_expense
        },
        'monthly_averages': monthly_averages,
        'transaction_count': transaction_count,
        'category_count': category_count,
        'spending_trend': {
            'recent': recent_total,
            'previous': previous_total,
            'change': spending_change,
            'change_percent': spending_change_percent
        }
    }
    
    return jsonify(summary)

@app.route('/api/reports/cashflow-projection', methods=['GET'])
@login_required
def cashflow_projection():
    # Get monthly averages to project future cash flow
    monthly_avg = transactions.aggregate([
        {'$match': {
            'user_id': session['user_id'],
            'date': {'$gte': datetime.utcnow() - timedelta(days=180)}  # Last 6 months
        }},
        {'$project': {
            'month': {'$month': '$date'},
            'amount': 1,
            'type': 1
        }},
        {'$group': {
            '_id': {'month': '$month', 'type': '$type'},
            'total': {'$sum': '$amount'}
        }},
        {'$group': {
            '_id': '$_id.type',
            'monthly_avg': {'$avg': '$total'}
        }}
    ])
    
    avg_income = 0
    avg_expense = 0
    
    for avg in monthly_avg:
        if avg['_id'] == 'income':
            avg_income = avg['monthly_avg']
        elif avg['_id'] == 'expense':
            avg_expense = avg['monthly_avg']
    
    # Project next 6 months
    projection = []
    now = datetime.utcnow()
    current_month = now.month
    current_year = now.year
    
    # Get current account balance
    all_income = transactions.aggregate([
        {'$match': {'user_id': session['user_id'], 'type': 'income'}},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    total_income = next(all_income, {}).get('total', 0)
    
    all_expense = transactions.aggregate([
        {'$match': {'user_id': session['user_id'], 'type': 'expense'}},
        {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
    ])
    total_expense = next(all_expense, {}).get('total', 0)
    
    current_balance = total_income - total_expense
    
    for i in range(6):
        month = current_month + i
        year = current_year
        
        while month > 12:
            month -= 12
            year += 1
            
        month_name = datetime(year, month, 1).strftime('%b %Y')
        
        projected_income = avg_income
        projected_expense = avg_expense
        projected_balance = current_balance + (avg_income - avg_expense) * (i + 1)
        
        projection.append({
            'month': month_name,
            'income': projected_income,
            'expense': projected_expense,
            'balance': projected_balance
        })
    
    return jsonify(projection)

@app.route('/budgets')
@login_required
def view_budgets():
    # Get all budgets for the user
    user_budgets = list(budgets.find({'user_id': session['user_id']}))
    
    # Calculate current spending for each budget
    current_month = datetime.utcnow().month
    current_year = datetime.utcnow().year
    start_of_month = datetime(current_year, current_month, 1)
    
    for budget in user_budgets:
        category_spending = transactions.aggregate([
            {'$match': {
                'user_id': session['user_id'],
                'category': budget['category'],
                'type': 'expense',
                'date': {'$gte': start_of_month}
            }},
            {'$group': {'_id': None, 'total': {'$sum': '$amount'}}}
        ])
        spent = next(category_spending, {}).get('total', 0)
        budget['spent'] = spent
        budget['remaining'] = budget['amount'] - spent
        budget['progress'] = min(100, int((spent / budget['amount']) * 100)) if budget['amount'] > 0 else 100
    
    # Get categories for new budget form
    user_settings = get_user_settings()
    expense_categories = user_settings['default_categories']['expense']
    
    return render_template('budgets.html', budgets=user_budgets, categories=expense_categories)

@app.route('/budgets/add', methods=['POST'])
@login_required
def add_budget():
    category = request.form['category']
    amount = float(request.form['amount'])
    
    # Check if budget for this category already exists
    existing_budget = budgets.find_one({
        'user_id': session['user_id'],
        'category': category
    })
    
    if existing_budget:
        # Update existing budget
        budgets.update_one(
            {'_id': existing_budget['_id']},
            {'$set': {
                'amount': amount,
                'updated_at': datetime.utcnow()
            }}
        )
        flash(f'Budget for {category} updated successfully')
    else:
        # Create new budget
        budgets.insert_one({
            'user_id': session['user_id'],
            'category': category,
            'amount': amount,
            'created_at': datetime.utcnow()
        })
        flash(f'Budget for {category} created successfully')
    
    return redirect(url_for('view_budgets'))

@app.route('/budgets/delete/<budget_id>', methods=['POST'])
@login_required
def delete_budget(budget_id):
    result = budgets.delete_one({
        '_id': ObjectId(budget_id),
        'user_id': session['user_id']
    })
    
    if result.deleted_count:
        flash('Budget deleted successfully')
    else:
        flash('Failed to delete budget')
        
    return redirect(url_for('view_budgets'))

@app.route('/settings', methods=['GET'])
def user_settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    message = request.args.get('message', '')
    message_type = request.args.get('type', 'info')
    
    return render_template('settings.html', message=message, message_type=message_type)

@app.route('/settings', methods=['POST'])
def update_settings():
    if 'user_id' not in session:
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    action = request.form.get('action', 'update_settings')
    
    # Handle different types of settings updates
    if action == 'update_settings':
        # Update general settings
        currency = request.form.get('currency')
        date_format = request.form.get('date_format')
        theme = request.form.get('theme')
        
        # Update categories if provided
        income_categories = request.form.get('income_categories', '')
        expense_categories = request.form.get('expense_categories', '')
        
        # Process email notification preferences
        email_notifications = {}
        notification_keys = request.form.getlist('email_notifications')
        
        # Set all notification types based on form submission
        all_notification_types = [
            'weekly_summary', 'budget_alerts', 'monthly_report', 
            'security_alerts', 'transaction_notifications'
        ]
        
        for key in all_notification_types:
            email_notifications[key] = key in notification_keys
        
        # Get current settings to update selectively
        settings = get_user_settings(user_id)
        
        # Update settings that were provided
        if currency:
            settings['currency'] = currency
        if date_format:
            settings['date_format'] = date_format
        if theme:
            settings['theme'] = theme
        
        # Only update categories if they were in the form
        if income_categories:
            income_list = [cat.strip() for cat in income_categories.split(',') if cat.strip()]
            settings['default_categories']['income'] = income_list
        
        if expense_categories:
            expense_list = [cat.strip() for cat in expense_categories.split(',') if cat.strip()]
            settings['default_categories']['expense'] = expense_list
        
        # Update email notifications if they were in the form
        if notification_keys:
            settings['email_notifications'] = email_notifications
        
        # Save updated settings
        users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'settings': settings}}
        )
        
        # Invalidate cache for this user
        get_user_settings.invalidate(user_id)
        
        flash('Settings updated successfully', 'success')
        return redirect(url_for('user_settings', message='Settings updated successfully', type='success'))
    
    elif action == 'change_password':
        # Handle password change
        current_password = request.form.get('current_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')
        
        # Validate passwords
        if not current_password or not new_password or not confirm_password:
            flash('All password fields are required', 'danger')
            return redirect(url_for('user_settings', message='All password fields are required', type='danger'))
        
        if new_password != confirm_password:
            flash('New passwords do not match', 'danger')
            return redirect(url_for('user_settings', message='New passwords do not match', type='danger'))
        
        if len(new_password) < 8:
            flash('New password must be at least 8 characters long', 'danger')
            return redirect(url_for('user_settings', message='New password must be at least 8 characters long', type='danger'))
        
        # Check current password
        user = users.find_one({'_id': ObjectId(user_id)})
        if not user or not bcrypt.checkpw(current_password.encode('utf-8'), user['password']):
            flash('Current password is incorrect', 'danger')
            return redirect(url_for('user_settings', message='Current password is incorrect', type='danger'))
        
        # Update password
        hashed_password = bcrypt.hashpw(new_password.encode('utf-8'), bcrypt.gensalt())
        users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'password': hashed_password}}
        )
        
        # Send email notification for password change
        user_email = user.get('email')
        if user_email and user.get('settings', {}).get('email_notifications', {}).get('security_alerts', True):
            html_content = f"""
            <h2>Password Changed</h2>
            <p>Hi {user.get('name', 'there')},</p>
            <p>Your password was just changed on your Finance Manager account.</p>
            <p>If you did not make this change, please contact support immediately.</p>
            <p>Time of change: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Thank you,<br>Finance Manager Team</p>
            """
            
            send_email(
                to_email=user_email,
                subject="Password Changed - Finance Manager",
                html_content=html_content
            )
        
        flash('Password updated successfully', 'success')
        return redirect(url_for('user_settings', message='Password updated successfully', type='success'))
    
    elif action == 'change_email':
        # Handle email change
        new_email = request.form.get('new_email')
        password = request.form.get('email_password')
        
        # Validate input
        if not new_email or not password:
            flash('Email and password are required', 'danger')
            return redirect(url_for('user_settings', message='Email and password are required', type='danger'))
        
        # Check if email is valid
        email_pattern = r'^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$'
        if not re.match(email_pattern, new_email):
            flash('Invalid email format', 'danger')
            return redirect(url_for('user_settings', message='Invalid email format', type='danger'))
        
        # Check if email already exists
        if users.find_one({'email': new_email, '_id': {'$ne': ObjectId(user_id)}}):
            flash('Email is already in use', 'danger')
            return redirect(url_for('user_settings', message='Email is already in use', type='danger'))
        
        # Check password
        user = users.find_one({'_id': ObjectId(user_id)})
        if not user or not bcrypt.checkpw(password.encode('utf-8'), user['password']):
            flash('Password is incorrect', 'danger')
            return redirect(url_for('user_settings', message='Password is incorrect', type='danger'))
        
        # Store old email for notification
        old_email = user.get('email')
        
        # Update email
        users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'email': new_email}}
        )
        
        # Update session
        session['user_email'] = new_email
        
        # Send email notifications
        if user.get('settings', {}).get('email_notifications', {}).get('security_alerts', True):
            # Notify new email
            html_content_new = f"""
            <h2>Email Address Changed</h2>
            <p>Hi {user.get('name', 'there')},</p>
            <p>Your email address for your Finance Manager account has been updated to {new_email}.</p>
            <p>If you did not make this change, please contact support immediately.</p>
            <p>Time of change: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Thank you,<br>Finance Manager Team</p>
            """
            
            send_email(
                to_email=new_email,
                subject="Email Address Changed - Finance Manager",
                html_content=html_content_new
            )
            
            # Notify old email if it exists
            if old_email:
                html_content_old = f"""
                <h2>Email Address Changed</h2>
                <p>Hi {user.get('name', 'there')},</p>
                <p>Your email address for your Finance Manager account has been changed from {old_email} to {new_email}.</p>
                <p>If you did not make this change, please contact support immediately.</p>
                <p>Time of change: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p>Thank you,<br>Finance Manager Team</p>
                """
                
                send_email(
                    to_email=old_email,
                    subject="Email Address Changed - Finance Manager",
                    html_content=html_content_old
                )
        
        flash('Email updated successfully', 'success')
        return redirect(url_for('user_settings', message='Email updated successfully', type='success'))
    
    # Default case
    flash('Unknown action', 'warning')
    return redirect(url_for('user_settings'))

@app.route('/api/test-email', methods=['POST'])
@login_required
@admin_required
def test_email():
    if 'user_id' not in session:
        return jsonify({'status': 'error', 'message': 'Not logged in'})
    
    user = users.find_one({'_id': ObjectId(session['user_id'])})
    if not user or not user.get('email'):
        return jsonify({'status': 'error', 'message': 'No email address found'})
    
    email = user['email']
    settings = get_user_settings()
    currency = settings.get('currency', 'USD')
    currency_symbol = settings.get('currency_symbols', {}).get(currency, '$')
    
    # Create test email with sample data
    html_content = f"""
    <html>
    <head>
        <style>
            body {{ font-family: 'Helvetica', 'Arial', sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px; }}
            h1, h2 {{ color: #3d5c9f; }}
            .container {{ border: 1px solid #ddd; border-radius: 8px; padding: 20px; box-shadow: 0 4px 8px rgba(0,0,0,0.1); }}
            .header {{ background-color: #3d5c9f; color: white; padding: 20px; border-radius: 8px 8px 0 0; margin: -20px -20px 20px; text-align: center; }}
            .footer {{ background-color: #f8f9fa; padding: 15px; border-radius: 0 0 8px 8px; margin: 20px -20px -20px; text-align: center; color: #666; font-size: 0.9em; }}
            .card {{ border: 1px solid #eee; border-radius: 8px; padding: 20px; margin-bottom: 20px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); background-color: #fff; }}
            .highlight {{ color: #3d5c9f; font-weight: bold; }}
            .text-success {{ color: #28a745; font-weight: bold; }}
            .text-danger {{ color: #dc3545; font-weight: bold; }}
            .amount {{ font-size: 1.2em; margin: 5px 0; }}
            .section-title {{ border-bottom: 2px solid #f0f0f0; padding-bottom: 8px; margin-top: 25px; color: #3d5c9f; }}
            .btn {{ display: inline-block; background-color: #3d5c9f; color: white; padding: 10px 20px; text-decoration: none; border-radius: 4px; margin-top: 15px; }}
            .btn:hover {{ background-color: #2d4b8e; }}
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1 style="margin: 0; padding: 0;">Finance Manager</h1>
            </div>
            
            <h2>Test Email</h2>
            <p>Hi {user.get('name', 'there')},</p>
            <p>This is a test email from your Finance Manager application. If you received this email, your email notifications are working correctly!</p>
            
            <h3 class="section-title">Sample Monthly Summary</h3>
            <div class="card">
                <p><span style="display: inline-block; width: 150px;">Total Income:</span> <span class="amount text-success">{currency_symbol}1,250.00</span></p>
                <p><span style="display: inline-block; width: 150px;">Total Expenses:</span> <span class="amount text-danger">{currency_symbol}950.00</span></p>
                <p><span style="display: inline-block; width: 150px;">Net Balance:</span> <span class="amount highlight">{currency_symbol}300.00</span></p>
                <p><span style="display: inline-block; width: 150px;">Top Spending:</span> Food ({currency_symbol}350.00)</p>
            </div>
            
            <h3 class="section-title">Recent Activity</h3>
            <div class="card">
                <p>You've had 8 transactions this month:</p>
                <ul>
                    <li>5 expense transactions</li>
                    <li>3 income transactions</li>
                </ul>
                <p>Your spending is 15% lower than last month!</p>
                <a href="#" class="btn">View Dashboard</a>
            </div>
            
            <p>You can configure your email preferences in the Settings page of your Finance Manager.</p>
            
            <div class="footer">
                <p>This is an automated message from Finance Manager. Please do not reply to this email.</p>
                <p>&copy; {datetime.now().year} Finance Manager. All rights reserved.</p>
            </div>
        </div>
    </body>
    </html>
    """
    
    # Send the test email
    success, message = send_email(
        to_email=email,
        subject="Test Email from Finance Manager",
        html_content=html_content
    )
    
    if success:
        return jsonify({'status': 'success', 'message': 'Test email sent successfully'})
    else:
        return jsonify({'status': 'error', 'message': message})

@app.route('/security-check')
@login_required
@admin_required
def security_check():
    """Security check endpoint for production readiness"""
    try:
        # Check for API keys in environment
        security_status = {
            'resend_api_configured': bool(RESEND_API_KEY),
            'secret_key_configured': app.secret_key != 'default_secret_key',
            'session_secure': app.config['SESSION_COOKIE_SECURE'],
            'db_connection': True,
            'email_workers': True
        }
        
        # Test MongoDB connection
        try:
            client.server_info()
        except:
            security_status['db_connection'] = False
        
        return jsonify(security_status)
    except Exception as e:
        app_logger.error(f"Security check error: {e}")
        return jsonify({'error': 'Error performing security check'})

# Helper to invalidate user settings cache when settings are updated
def invalidate_settings_cache(user_id):
    """Clear the cache for a specific user when their settings change"""
    # Create the same cache key that would be used by the cached decorator
    key_parts = ["get_user_settings", str(user_id)]
    cache_key = hashlib.md5(":".join(key_parts).encode()).hexdigest()
    
    # Find the cache in the cached decorator's wrapper
    wrapper = get_user_settings
    if hasattr(wrapper, 'cache') and cache_key in wrapper.cache:
        del wrapper.cache[cache_key]

# Helper function to get user's email preferences
def should_send_email(user_id, notification_type):
    """Check if a user has enabled a specific email notification type"""
    if not user_id:
        return False
        
    try:
        settings_doc = get_user_settings(user_id)
        if not settings_doc:
            return False
            
        return settings_doc.get('email_notifications', {}).get(notification_type, False)
    except Exception as e:
        app_logger.error(f"Error checking email preferences: {e}")
        return False

# Add log viewer for admins
@app.route('/admin/logs')
@admin_required
def view_logs():
    """Admin interface for viewing application logs"""
    try:
        # In a serverless environment, we can't read log files
        # Instead, we'll show a message about logging configuration
        return render_template('admin/logs.html',
                             title='Application Logs',
                             logs=['Logs are not available in serverless environment.'],
                             current_page=1,
                             total_pages=1,
                             log_type='app',
                             max=max,
                             min=min)
                             
    except Exception as e:
        app_logger.error(f"Error accessing logs: {e}")
        flash('Error accessing logs', 'error')
        return redirect(url_for('dashboard'))

# Add endpoint to clear logs with admin authentication
@app.route('/admin/clear-logs', methods=['POST'])
@login_required
@admin_required
def clear_logs():
    """Clear logs with admin authentication"""
    try:
        if not is_admin(session['user_id']):
            return jsonify({'success': False, 'message': 'Admin privileges required'})
            
        # Get data from request
        data = request.get_json()
        log_type = data.get('log_type', 'app')
        password = data.get('password', '')
        
        # Additional security: verify admin password
        admin_password = os.environ.get('ADMIN_PASSWORD')
        if not admin_password or not password or password != admin_password:
            app_logger.warning(f"Failed attempt to clear logs with invalid admin password by user ID: {session['user_id']}")
            return jsonify({'success': False, 'message': 'Invalid admin password'})
        
        # In a serverless environment, we can't clear log files
        # Instead, we'll just log the attempt
        app_logger.info(f"Log clear request received from admin (user ID: {session['user_id']})")
        
        return jsonify({
            'success': True, 
            'message': 'Log clearing is not available in serverless environment. Logs are managed by the platform.'
        })
    except Exception as e:
        app_logger.error(f"Error clearing logs: {e}")
        return jsonify({'success': False, 'message': f'Error clearing logs: {str(e)}'})

# Add user management routes for admins
@app.route('/admin/users', methods=['GET'])
@login_required
@admin_required
def admin_users():
    """Admin interface for managing users and admin privileges"""
    try:
        # Get all users from the database
        all_users = list(users.find({}, {
            'email': 1, 
            'created_at': 1, 
            'last_login': 1, 
            'is_admin': 1
        }))
        
        # Count total admins
        admin_count = sum(1 for user in all_users if user.get('is_admin', False))
        
        return render_template('admin/users.html', 
                              users=all_users,
                              admin_count=admin_count)
    except Exception as e:
        app_logger.error(f"Error viewing users: {e}")
        flash(f"Error viewing users: {str(e)}", "danger")
        return redirect(url_for('dashboard'))

@app.route('/admin/users/toggle-admin/<user_id>', methods=['POST'])
@login_required
@admin_required
def toggle_admin(user_id):
    """Toggle admin privileges for a user"""
    try:
        # Verify the admin password for this sensitive operation
        password = request.form.get('admin_password', '')
        admin_password = os.environ.get('ADMIN_PASSWORD')
        
        if not admin_password or password != admin_password:
            flash('Invalid admin password', 'danger')
            return redirect(url_for('admin_users'))
        
        # Find the target user
        target_user = users.find_one({'_id': ObjectId(user_id)})
        if not target_user:
            flash('User not found', 'danger')
            return redirect(url_for('admin_users'))
        
        # Toggle the admin status
        current_status = target_user.get('is_admin', False)
        new_status = not current_status
        
        # Update the user document
        users.update_one(
            {'_id': ObjectId(user_id)},
            {'$set': {'is_admin': new_status}}
        )
        
        # Log the change
        action = "granted" if new_status else "revoked"
        app_logger.info(f"Admin privileges {action} for user {target_user.get('email')} by admin {session['user_id']}")
        
        status_msg = "granted to" if new_status else "revoked from"
        flash(f"Admin privileges {status_msg} {target_user.get('email')}", 'success')
        
        return redirect(url_for('admin_users'))
    except Exception as e:
        app_logger.error(f"Error toggling admin status: {e}")
        flash(f"Error toggling admin status: {str(e)}", "danger")
        return redirect(url_for('admin_users'))

# Route to initialize the first admin user when none exists
@app.route('/initialize-admin', methods=['GET', 'POST'])
def initialize_admin():
    """Initialize the first admin user when the system is new"""
    try:
        # Check if there are any admin users already
        admin_exists = users.find_one({'is_admin': True})
        
        # If admins already exist, redirect to login
        if admin_exists:
            flash('Admin users already exist in the system', 'info')
            return redirect(url_for('login'))
        
        # Check if there are any users at all
        any_users = users.find_one({})
        
        if request.method == 'POST':
            # Validate setup code from environment
            setup_code = os.environ.get('ADMIN_SETUP_CODE')
            if not setup_code or request.form.get('setup_code') != setup_code:
                flash('Invalid setup code', 'danger')
                return render_template('admin/initialize.html', has_users=bool(any_users))
            
            email = request.form.get('email')
            password = request.form.get('password')
            
            # Validate input
            if not email or not password:
                flash('Email and password are required', 'danger')
                return render_template('admin/initialize.html', has_users=bool(any_users))
            
            if len(password) < 8:
                flash('Password must be at least 8 characters long', 'danger')
                return render_template('admin/initialize.html', has_users=bool(any_users))
            
            # Check if the user exists
            existing_user = users.find_one({'email': email})
            
            if existing_user:
                # Promote existing user to admin
                users.update_one(
                    {'_id': existing_user['_id']},
                    {'$set': {'is_admin': True}}
                )
                app_logger.info(f"Existing user {email} promoted to admin during initialization")
                flash(f"User {email} has been promoted to admin", 'success')
                return redirect(url_for('login'))
            else:
                # Create new admin user
                hashed_password = bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt())
                
                user_id = users.insert_one({
                    'email': email,
                    'password': hashed_password,
                    'created_at': datetime.utcnow(),
                    'is_admin': True,
                    'settings': {
                        'currency': 'USD',
                        'date_format': '%Y-%m-%d',
                        'theme': 'light',
                        'default_categories': {
                            'income': ['Salary', 'Freelance', 'Gifts', 'Investments', 'Other'],
                            'expense': ['Food', 'Housing', 'Transportation', 'Utilities', 'Entertainment', 'Healthcare', 'Education', 'Shopping', 'Personal', 'Other']
                        },
                        'email_notifications': {
                            'weekly_summary': True,
                            'budget_alerts': True,
                            'security_alerts': True,
                            'monthly_report': False,
                            'transaction_notifications': False
                        }
                    }
                }).inserted_id
                
                app_logger.info(f"New admin user {email} created during initialization")
                flash(f"Admin user {email} has been created", 'success')
                return redirect(url_for('login'))
        
        # GET request - show the initialization form
        return render_template('admin/initialize.html', has_users=bool(any_users))
        
    except Exception as e:
        app_logger.error(f"Error initializing admin: {e}")
        flash(f"Error initializing admin: {str(e)}", "danger")
        return redirect(url_for('index'))

if __name__ == '__main__':
    # Start email worker threads
    email_workers = start_email_workers(num_workers=2)
    
    # Use WSGI server in production, development server for local development
    if os.environ.get('FLASK_ENV') == 'production':
        # In production, this should be run with a WSGI server like gunicorn
        app_logger.info("Running in production mode - use with WSGI server")
    else:
        # For development only
        app.run(debug=os.environ.get('FLASK_DEBUG', 'False').lower() == 'true')
    
    # Shutdown email workers gracefully
    for _ in range(len(email_workers)):
        email_queue.put(None)
    for worker in email_workers:
        worker.join(timeout=5) 
