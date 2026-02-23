import os

class Config:
    SECRET_KEY = "dev-secret-change-me"
    SQLALCHEMY_DATABASE_URI = "sqlite:///local.db"
    SQLALCHEMY_TRACK_MODIFICATIONS = False