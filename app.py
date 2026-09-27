import os
from datetime import datetime, timezone

from flask import Flask, request
from flask_sqlalchemy import SQLAlchemy

db = SQLAlchemy()


class Visit(db.Model):
    __tablename__ = "visits"

    id = db.Column(db.Integer, primary_key=True)
    visited_at = db.Column(db.DateTime, nullable=False)
    ip_address = db.Column(db.String(45), nullable=False)


def create_app():
    app = Flask(__name__)

    db_user = os.getenv("DB_USER")
    db_password = os.getenv("DB_PASSWORD")
    db_host = os.getenv("DB_HOST")
    db_port = os.getenv("DB_PORT", "5432")
    db_name = os.getenv("DB_NAME")

    app.config["SQLALCHEMY_DATABASE_URI"] = (
        f"postgresql+psycopg://{db_user}:{db_password}@"
        f"{db_host}:{db_port}/{db_name}"
    )
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    db.init_app(app)

    with app.app_context():
        db.create_all()

    @app.get("/hello")
    def hello():
        visit = Visit(
            visited_at=datetime.now(timezone.utc),
            ip_address=request.remote_addr or "unknown",
        )

        db.session.add(visit)
        db.session.commit()

        return "Hello", 200

    return app


app = create_app()