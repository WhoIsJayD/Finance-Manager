import os
from datetime import datetime

import pandas as pd
import plotly.express as px
from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, flash, session
from flask_pymongo import PyMongo
from pymongo.errors import ConnectionFailure
from werkzeug.security import generate_password_hash, check_password_hash

load_dotenv()

app = Flask(__name__)
app.config["MONGO_URI"] = os.getenv("MONGO_URI")
app.secret_key = os.getenv("SECRET_KEY")

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
initial_config = {
    'layout': 'default',
    'currency': '$',
    'theme': 'light',
    'categories': ['Food', 'Transportation', 'Shopping', 'Utilities'],
    'sources': ['Salary', 'Investment', 'Savings']
}


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

        mongo.db.config.insert_one({
            "user_id": user_id,
            'layout': 'default',
            'currency': '$',
            'theme': 'light',
            'categories': ['Food', 'Transportation', 'Shopping', 'Utilities'],
            'sources': ['Salary', 'Investment', 'Savings']
        })

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
    session.pop("user_id", None)
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
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

    expense_chart = generate_expense_chart(expenses)
    income_chart = generate_income_chart(incomes)
    balance_trend = generate_balance_trend(expenses, incomes)

    return render_template("dashboard.html",
                           total_expense=total_expense,
                           total_income=total_income,
                           balance=balance,
                           expense_chart=expense_chart,
                           income_chart=income_chart,
                           balance_trend=balance_trend,
                           config=mongo.db.config.find_one({"user_id": user_id}))


@app.route("/add_expense", methods=["GET", "POST"])
def add_expense():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]
    incomes = list(mongo.db.incomes.find({"user_id": user_id}))
    expenses = list(mongo.db.expenses.find({"user_id": user_id}))

    total_expense = sum(expense.get("amount", 0) for expense in expenses)
    total_income = sum(income.get("amount", 0) for income in incomes)
    balance = total_income - total_expense

    if request.method == "POST":
        amount = float(request.form.get("amount"))
        category = request.form.get("category")
        date_str = request.form.get("date")
        date = parse_date(date_str)

        if date is None:
            flash("Invalid date format. Please use YYYY-MM-DD.")
            return redirect(url_for("add_expense"))

        if amount > balance:
            flash("Insufficient balance to add this expense")
            return redirect(url_for("add_expense"))

        mongo.db.expenses.insert_one({
            "user_id": user_id,
            "amount": amount,
            "category": category,
            "date": date
        })

        flash("Expense added successfully")
        return redirect(url_for("dashboard"))

    _config = mongo.db.config.find_one({"user_id": user_id})
    return render_template("add_expense.html", config=_config)


@app.route("/add_income", methods=["GET", "POST"])
def add_income():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))
    user_id = session["user_id"]

    if request.method == "POST":
        user_id = session["user_id"]
        amount = float(request.form.get("amount"))
        source = request.form.get("source")
        date_str = request.form.get("date")
        date = parse_date(date_str)

        if date is None:
            flash("Invalid date format. Please use YYYY-MM-DD.")
            return redirect(url_for("add_income"))

        mongo.db.incomes.insert_one({
            "user_id": user_id,
            "amount": amount,
            "source": source,
            "date": date
        })

        flash("Income added successfully")
        return redirect(url_for("dashboard"))

    _config = mongo.db.config.find_one({"user_id": user_id})
    return render_template("add_income.html", config=_config)


@app.route("/reports")
def reports():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session["user_id"]
    expenses = list(mongo.db.expenses.find({"user_id": user_id}))
    incomes = list(mongo.db.incomes.find({"user_id": user_id}))

    monthly_report = generate_monthly_report(expenses, incomes)
    yearly_report = generate_yearly_report(expenses, incomes)
    weekly_report = generate_weekly_report(expenses, incomes)

    user_id = session["user_id"]
    _config = mongo.db.config.find_one({"user_id": user_id})
    return render_template("reports.html",
                           monthly_report=monthly_report,
                           yearly_report=yearly_report,
                           weekly_report=weekly_report,
                           config=_config)


@app.route('/config', methods=['GET', 'POST'])
def config():
    if 'user_id' not in session:
        flash('You need to log in first', 'error')
        return redirect(url_for('login'))

    user_id = session['user_id']

    if request.method == 'POST':
        layout = request.form['layout']
        currency = request.form['currency']
        theme = request.form['theme']
        categories = [cat.strip() for cat in request.form['categories'].split(',')]
        sources = [source.strip() for source in request.form['sources'].split(',')]

        # Update MongoDB with new configuration
        mongo.db.config.update_one(
            {'user_id': user_id},
            {'$set': {
                'layout': layout,
                'currency': currency,
                'theme': theme,
                'categories': categories,
                'sources': sources
            }},
            upsert=True
        )

        flash('Configuration saved successfully', 'success')
        return redirect(url_for('config'))

    # Fetch current configuration from MongoDB
    user_config = mongo.db.config.find_one({'user_id': user_id})
    if not user_config:
        user_config = initial_config

    return render_template('config.html', config=user_config)


def generate_expense_chart(expenses):
    df = pd.DataFrame(expenses)
    if df.empty or 'date' not in df.columns:
        return "No data available"
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    fig = px.pie(df, values="amount", names="category", title="Expense Distribution")
    return fig.to_html(full_html=False)


def generate_income_chart(incomes):
    df = pd.DataFrame(incomes)
    if df.empty or 'date' not in df.columns:
        return "No data available"
    df['date'] = pd.to_datetime(df['date'], errors='coerce')
    df = df.dropna(subset=['date'])
    fig = px.bar(df, x="source", y="amount", title="Income Sources")
    return fig.to_html(full_html=False)


def generate_balance_trend(expenses, incomes):
    df_expenses = pd.DataFrame(expenses)
    df_incomes = pd.DataFrame(incomes)

    if df_expenses.empty or df_incomes.empty or 'date' not in df_expenses.columns or 'date' not in df_incomes.columns:
        return "No data available"

    df_expenses["date"] = pd.to_datetime(df_expenses['date'], errors='coerce')
    df_expenses = df_expenses.dropna(subset=['date'])

    df_incomes["date"] = pd.to_datetime(df_incomes['date'], errors='coerce')
    df_incomes = df_incomes.dropna(subset=['date'])

    df_balance = pd.concat([df_expenses.assign(type="expense"), df_incomes.assign(type="income")])
    df_balance["date"] = pd.to_datetime(df_balance["date"])
    df_balance.sort_values("date", inplace=True)

    df_balance["amount"] = df_balance.apply(lambda x: -x["amount"] if x["type"] == "expense" else x["amount"], axis=1)
    df_balance["cumulative_amount"] = df_balance["amount"].cumsum()

    fig = px.line(df_balance, x="date", y="cumulative_amount", title="Balance Trend")
    return fig.to_html(full_html=False)


def generate_monthly_report(expenses, incomes):
    df_expenses = pd.DataFrame(expenses)
    df_incomes = pd.DataFrame(incomes)

    if df_expenses.empty or df_incomes.empty or 'date' not in df_expenses.columns or 'date' not in df_incomes.columns:
        return "No data available"

    df_expenses["date"] = pd.to_datetime(df_expenses['date'], errors='coerce')
    df_expenses = df_expenses.dropna(subset=['date'])

    df_incomes["date"] = pd.to_datetime(df_incomes['date'], errors='coerce')
    df_incomes = df_incomes.dropna(subset=['date'])

    df_expenses["month"] = df_expenses["date"].dt.to_period("M").astype(str)
    df_incomes["month"] = df_incomes["date"].dt.to_period("M").astype(str)

    monthly_expenses = df_expenses.groupby("month")["amount"].sum()
    monthly_incomes = df_incomes.groupby("month")["amount"].sum()

    monthly_balance = monthly_incomes - monthly_expenses

    fig = px.bar(monthly_balance, title="Monthly Balance")
    return fig.to_html(full_html=False)


def generate_yearly_report(expenses, incomes):
    df_expenses = pd.DataFrame(expenses)
    df_incomes = pd.DataFrame(incomes)

    if df_expenses.empty or df_incomes.empty or 'date' not in df_expenses.columns or 'date' not in df_incomes.columns:
        return "No data available"

    df_expenses["date"] = pd.to_datetime(df_expenses['date'], errors='coerce')
    df_expenses = df_expenses.dropna(subset=['date'])

    df_incomes["date"] = pd.to_datetime(df_incomes['date'], errors='coerce')
    df_incomes = df_incomes.dropna(subset=['date'])

    df_expenses["year"] = df_expenses["date"].dt.year.astype(str)
    df_incomes["year"] = df_incomes["date"].dt.year.astype(str)

    yearly_expenses = df_expenses.groupby("year")["amount"].sum()
    yearly_incomes = df_incomes.groupby("year")["amount"].sum()

    yearly_balance = yearly_incomes - yearly_expenses

    fig = px.bar(yearly_balance, title="Yearly Balance")
    return fig.to_html(full_html=False)


def generate_weekly_report(expenses, incomes):
    df_expenses = pd.DataFrame(expenses)
    df_incomes = pd.DataFrame(incomes)

    if df_expenses.empty or df_incomes.empty or 'date' not in df_expenses.columns or 'date' not in df_incomes.columns:
        return "No data available"

    df_expenses["date"] = pd.to_datetime(df_expenses['date'], errors='coerce')
    df_expenses = df_expenses.dropna(subset=['date'])

    df_incomes["date"] = pd.to_datetime(df_incomes['date'], errors='coerce')
    df_incomes = df_incomes.dropna(subset=['date'])

    df_expenses["week"] = df_expenses["date"].dt.to_period("W").astype(str)
    df_incomes["week"] = df_incomes["date"].dt.to_period("W").astype(str)

    weekly_expenses = df_expenses.groupby("week")["amount"].sum()
    weekly_incomes = df_incomes.groupby("week")["amount"].sum()

    weekly_balance = weekly_incomes - weekly_expenses

    fig = px.bar(weekly_balance, title="Weekly Balance")
    return fig.to_html(full_html=False)


def parse_date(date_str):
    try:
        return datetime.strptime(date_str, '%Y-%m-%d')
    except ValueError:
        return None


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
