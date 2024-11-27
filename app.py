import os
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor

import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_caching import Cache
from flask_pymongo import PyMongo
from pymongo.errors import ConnectionFailure
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Load environment variables
load_dotenv()

# Flask app setup
app = Flask(__name__)
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
app.secret_key = os.getenv("SECRET_KEY")
app.config["CACHE_TYPE"] = "simple"
app.config["CACHE_DEFAULT_TIMEOUT"] = 300

cache = Cache(app)

# Configure Flask-Limiter
limiter = Limiter(
    get_remote_address,
    app=app,
    storage_uri=os.getenv("MONGO_URI")
)

# MongoDB setup
try:
    mongo = PyMongo(app)
    mongo.db.command('ping')
    print("Connected to MongoDB!")

    mongo.db.users.create_index("email", unique=True)
    mongo.db.expenses.create_index([("user_id", 1), ("date", -1)])
    mongo.db.incomes.create_index([("user_id", 1), ("date", -1)])
    mongo.db.config.create_index("user_id", unique=True)
except ConnectionFailure as e:
    print(f"Failed to connect to MongoDB: {e}")
    mongo = None

# Default configuration
initial_config = {
    'layout': 'default',
    'currency': '$',
    'theme': 'light',
    'categories': ['Food', 'Transportation', 'Shopping', 'Utilities'],
    'sources': ['Salary', 'Investment', 'Savings']
}


def parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None


def generate_chart(df, chart_type, **kwargs):
    if df.empty or 'date' not in df.columns:
        return "No data available"
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    if chart_type == "pie":
        return px.pie(df, **kwargs).to_html(full_html=False)
    elif chart_type == "bar":
        return px.bar(df, **kwargs).to_html(full_html=False)
    elif chart_type == "line":
        return px.line(df, **kwargs).to_html(full_html=False)
    return "Invalid chart type"


@app.before_request
def before_request():
    if 'config' not in session:
        session['config'] = initial_config


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if "user_id" in session:
        flash("You are already logged in!")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email").lower()
        password = request.form.get("password")

        if mongo.db.users.find_one({"email": email}):
            flash("Email already exists")
            return redirect(url_for("signup"))

        hashed_password = generate_password_hash(password)
        user_id = mongo.db.users.insert_one({"email": email, "password": hashed_password}).inserted_id

        mongo.db.config.insert_one({"user_id": user_id, **initial_config})
        flash("Account created successfully")
        return redirect(url_for("login"))

    return render_template("signup.html")


@app.route("/login", methods=["GET", "POST"])
def login():
    if "user_id" in session:
        flash("You are already logged in!")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        email = request.form.get("email").lower()
        password = request.form.get("password")
        user = mongo.db.users.find_one({"email": email})

        if user and check_password_hash(user["password"], password):
            session["user_id"] = str(user["_id"])
            return redirect(url_for("dashboard"))

        flash("Invalid email or password")

    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@cache.cached(timeout=60, query_string=True)
def dashboard():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]
    expenses = list(mongo.db.expenses.find({"user_id": user_id}))
    incomes = list(mongo.db.incomes.find({"user_id": user_id}))

    total_expense = sum(expense.get("amount", 0) for expense in expenses)
    total_income = sum(income.get("amount", 0) for income in incomes)
    balance = total_income - total_expense

    expense_df = pd.DataFrame(expenses)
    income_df = pd.DataFrame(incomes)

    expense_chart = generate_chart(expense_df, "pie", values="amount", names="category", title="Expense Distribution")
    income_chart = generate_chart(income_df, "bar", x="source", y="amount", title="Income Sources")

    balance_trend = generate_chart(
        pd.concat([
            expense_df.assign(type="expense", amount=-expense_df["amount"]),
            income_df.assign(type="income")
        ]),
        "line", x="date", y="amount", title="Balance Trend"
    )

    return render_template("dashboard.html",
                           total_expense=total_expense,
                           total_income=total_income,
                           balance=balance,
                           expense_chart=expense_chart,
                           income_chart=income_chart,
                           balance_trend=balance_trend)


@app.route("/add_expense", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def add_expense():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]

    if request.method == "POST":
        amount = float(request.form.get("amount"))
        category = request.form.get("category")
        date = parse_date(request.form.get("date"))

        if not date:
            flash("Invalid date format. Please use YYYY-MM-DD.")
            return redirect(url_for("add_expense"))

        mongo.db.expenses.insert_one({"user_id": user_id, "amount": amount, "category": category, "date": date})
        flash("Expense added successfully")
        return redirect(url_for("dashboard"))

    config = mongo.db.config.find_one({"user_id": user_id}) or initial_config
    return render_template("add_expense.html", config=config)


@app.route("/add_income", methods=["GET", "POST"])
@limiter.limit("5 per minute")
def add_income():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]

    if request.method == "POST":
        amount = float(request.form.get("amount"))
        source = request.form.get("source")
        date = parse_date(request.form.get("date"))

        if not date:
            flash("Invalid date format. Please use YYYY-MM-DD.")
            return redirect(url_for("add_income"))

        mongo.db.incomes.insert_one({"user_id": user_id, "amount": amount, "source": source, "date": date})
        flash("Income added successfully")
        return redirect(url_for("dashboard"))

    config = mongo.db.config.find_one({"user_id": user_id}) or initial_config
    return render_template("add_income.html", config=config)


@app.route("/config", methods=["GET", "POST"])
def config():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]

    if request.method == "POST":
        layout = request.form["layout"]
        currency = request.form["currency"]
        theme = request.form["theme"]
        categories = [c.strip() for c in request.form["categories"].split(",")]
        sources = [s.strip() for s in request.form["sources"].split(",")]

        mongo.db.config.update_one(
            {"user_id": user_id},
            {"$set": {"layout": layout, "currency": currency, "theme": theme, "categories": categories, "sources": sources}},
            upsert=True
        )
        flash("Configuration updated successfully")
        return redirect(url_for("config"))

    config = mongo.db.config.find_one({"user_id": user_id}) or initial_config
    return render_template("config.html", config=config)

@app.after_request
def add_security_headers(response):
    response.headers['X-Frame-Options'] = 'DENY'
    return response

@app.route("/health")
def health():
    try:
        mongo.db.command("ping")
        return "MongoDB connection: OK", 200
    except Exception:
        return "MongoDB connection: Failed", 500


if __name__ == "__main__":
    app.run(host="0.0.0.0",debug=True)
