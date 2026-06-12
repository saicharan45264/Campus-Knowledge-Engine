import sys
import os

# Add project root to python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.db.connection import get_pg_connection
from src.api.auth_utils import get_password_hash

def init_system():
    conn = get_pg_connection()
    try:
        with conn.cursor() as cur:
            # 1. Create a university
            cur.execute("""
                INSERT INTO universities (name, short_code) 
                VALUES ('Amrita Vishwa Vidyapeetham', 'AMRITA_CB')
                ON CONFLICT (short_code) DO NOTHING
                RETURNING id
            """)
            row = cur.fetchone()
            if row:
                univ_id = row[0]
            else:
                cur.execute("SELECT id FROM universities WHERE short_code = 'AMRITA_CB'")
                univ_id = cur.fetchone()[0]

            # 2. Create the default admin account
            admin_username = "admin@amrita.edu"
            admin_password = "adminpassword123"
            hashed_pw = get_password_hash(admin_password)

            cur.execute("""
                INSERT INTO admin_users (university_id, username, password_hash)
                VALUES (%s, %s, %s)
                ON CONFLICT (username) DO NOTHING
            """, (univ_id, admin_username, hashed_pw))
            
            conn.commit()
            print("System initialized.")
            print(f"University ID: {univ_id}")
            print(f"Admin Username: {admin_username}")
            print(f"Admin Password: {admin_password}")
    finally:
        conn.close()

if __name__ == "__main__":
    init_system()
