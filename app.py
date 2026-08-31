from flask import Flask, render_template

import database as db
from config import Config
from routes.appointment_routes import bp as appointment_bp
from routes.auth_routes import bp as auth_bp
from routes.availability_routes import bp as availability_bp
from routes.user_routes import bp as user_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)

    db.init_app(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(user_bp)
    app.register_blueprint(availability_bp)
    app.register_blueprint(appointment_bp)

    with app.app_context():
        db.init_db()

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
