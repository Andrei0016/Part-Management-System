from datetime import datetime, timezone

from flask_login import UserMixin
from werkzeug.security import check_password_hash, generate_password_hash

from app.extensions import db


class Box(db.Model):
    __tablename__ = "box"

    box_id = db.Column(db.String(20), primary_key=True)
    shelf = db.Column(db.String(50), nullable=False)
    row = db.Column(db.String(50), nullable=False)

    parts = db.relationship("Part", backref="box", lazy=True, order_by="Part.id")

    def __repr__(self):
        return f"<Box {self.box_id}>"


class Part(db.Model):
    __tablename__ = "part"

    id = db.Column(db.String(50), primary_key=True)  # box_id-part_id, e.g. 001-001
    box_id = db.Column(db.String(20), db.ForeignKey("box.box_id"), nullable=False)
    name = db.Column(db.String(200), nullable=False)
    quantity = db.Column(db.Integer, nullable=False, default=0)
    minimum_quantity = db.Column(db.Integer, nullable=False, default=0)
    tags = db.Column(db.String(300), nullable=False, default="")  # comma-separated

    @property
    def tag_list(self):
        return [t.strip() for t in self.tags.split(",") if t.strip()]

    @property
    def is_low_stock(self):
        return self.quantity < self.minimum_quantity

    def __repr__(self):
        return f"<Part {self.id} {self.name}>"


class User(UserMixin, db.Model):
    __tablename__ = "user"

    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(80), unique=True, nullable=False)
    full_name = db.Column(db.String(120), nullable=False)
    password_hash = db.Column(db.String(255), nullable=False)
    is_admin = db.Column(db.Boolean, nullable=False, default=False)
    is_active = db.Column(db.Boolean, nullable=False, default=True)

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def __repr__(self):
        return f"<User {self.username}>"


class LogEntry(db.Model):
    __tablename__ = "log_entry"

    id = db.Column(db.Integer, primary_key=True)
    timestamp = db.Column(db.DateTime, nullable=False, default=lambda: datetime.now(timezone.utc))
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)
    action = db.Column(db.String(20), nullable=False)  # take / add / register / edit
    part_id = db.Column(db.String(50), nullable=True)
    quantity_delta = db.Column(db.Integer, nullable=True)
    note = db.Column(db.String(300), nullable=True)

    user = db.relationship("User")


class SyncState(db.Model):
    __tablename__ = "sync_state"

    id = db.Column(db.Integer, primary_key=True)
    dirty = db.Column(db.Boolean, nullable=False, default=True)
    last_synced_at = db.Column(db.DateTime, nullable=True)

    @classmethod
    def get(cls):
        state = cls.query.first()
        if state is None:
            state = cls(dirty=True, last_synced_at=None)
            db.session.add(state)
            db.session.commit()
        return state

    @classmethod
    def mark_dirty(cls):
        state = cls.get()
        state.dirty = True
        db.session.commit()
