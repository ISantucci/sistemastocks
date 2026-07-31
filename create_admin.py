#!/usr/bin/env python3
"""Script para crear usuario admin en la BD"""

import sys
import os
from pathlib import Path

# Agregar app al path
sys.path.insert(0, str(Path(__file__).parent))

from app import app, db, User

def create_admin_user():
    with app.app_context():
        # Verificar si admin existe
        existing = User.query.filter_by(username="admin").first()
        if existing:
            print("✗ Usuario 'admin' ya existe")
            return False

        # Crear usuario
        admin = User(
            username="admin",
            full_name="Administrador",
            role="ADMIN"
        )
        admin.set_password("admin")

        db.session.add(admin)
        db.session.commit()

        print("✓ Usuario 'admin' creado con éxito")
        print("  Username: admin")
        print("  Password: admin")
        print("  Role: ADMIN")
        return True

if __name__ == "__main__":
    create_admin_user()
