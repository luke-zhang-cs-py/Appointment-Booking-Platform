import logging

from flask import Flask, render_template

import database as db
import mailer
import notifications
from config import Config
from routes.appointment_routes import bp as appointment_bp
from routes.auth_routes import bp as auth_bp
from routes.availability_routes import bp as availability_bp
from routes.email_routes import bp as email_bp
from routes.user_routes import bp as user_bp


def create_app():
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )

    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)
    mailer.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(availability_bp)
    app.register_blueprint(appointment_bp)
    app.register_blueprint(email_bp)

    with app.app_context():
        db.init_db()

    notifications.start_reminder_scheduler(app)

    @app.get("/")
    def index():
        return render_template("index.html")

    @app.errorhandler(404)
    def not_found(_e):
        from flask import jsonify, request

        if request.path.startswith("/api/"):
            return jsonify({"error": "Not found"}), 404
        return render_template("index.html")

    return app


app = create_app()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=Config.PORT, debug=Config.DEBUG)
