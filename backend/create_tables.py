from app.db.initialize import initialize_database


if __name__ == "__main__":
    initialize_database(migrate=True)
    print("Database schema is migrated and SmartCity departments are ready.")
