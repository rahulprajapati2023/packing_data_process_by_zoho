import pandas as pd
import pymysql
from sqlalchemy import create_engine, text
from datetime import datetime
import imaplib
import email
from email.header import decode_header
import io
import warnings
import numpy as np

warnings.filterwarnings('ignore')

# Database credentials
DB_CONFIG = {
    'host': '103.195.186.17',
    'port': 3306,
    'database': 'wt_marketing',
    'user': 'rahul',
    'password': 't3#Zw390r'
}

# Gmail credentials
GMAIL_USER = 'rahulprajapati@whiteteak.com'
GMAIL_APP_PASSWORD = 'dapd vfjm zklq dggv'


def fetch_csv_from_gmail():
    """Fetch CSV attachment from latest email with exact subject line"""
    print("📧 Connecting to Gmail...")

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com")
        mail.login(GMAIL_USER, GMAIL_APP_PASSWORD)
        mail.select("inbox")

        exact_subject = 'Scheduled Daily Report, Packing History, from U/O Obgenix Software Pvt Ltd'
        search_criteria = f'(SUBJECT "{exact_subject}")'
        status, messages = mail.search(None, search_criteria)

        if status != 'OK' or not messages[0]:
            print(f"❌ No emails found with subject: {exact_subject}")
            return None

        latest_email_id = messages[0].split()[-1]
        status, msg_data = mail.fetch(latest_email_id, "(RFC822)")

        if status != 'OK':
            print("❌ Failed to fetch email")
            return None

        for response_part in msg_data:
            if isinstance(response_part, tuple):
                msg = email.message_from_bytes(response_part[1])
                subject = decode_header(msg["Subject"])[0][0]
                if isinstance(subject, bytes):
                    subject = subject.decode()
                print(f"📨 Found email: {subject[:100]}...")

                for part in msg.walk():
                    if part.get_content_maintype() == 'multipart':
                        continue
                    if part.get('Content-Disposition') is None:
                        continue

                    filename = part.get_filename()
                    if filename and filename.endswith('.csv'):
                        print(f"📎 Found attachment: {filename}")
                        payload = part.get_payload(decode=True)
                        return payload.decode('utf-8', errors='ignore')

        print("❌ No CSV attachment found")
        return None

    except Exception as e:
        print(f"❌ Gmail error: {e}")
        return None
    finally:
        try:
            mail.close()
            mail.logout()
        except:
            pass


def create_mysql_connection():
    """Create MySQL connection"""
    connection_string = f"mysql+pymysql://{DB_CONFIG['user']}:{DB_CONFIG['password']}@{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['database']}?charset=utf8mb4"
    engine = create_engine(connection_string)
    return engine


def create_table(engine):
    """Create table if not exists"""
    create_table_sql = text("""
    CREATE TABLE IF NOT EXISTS packing_details (
        package_id VARCHAR(100) PRIMARY KEY,
        salesorder_id VARCHAR(100),
        shipment_id VARCHAR(100),
        customer_id VARCHAR(100),
        customer_name TEXT,
        status VARCHAR(50),
        package_number VARCHAR(100),
        tracking_number TEXT,
        is_tracking_enabled BOOLEAN DEFAULT FALSE,
        shipment_type VARCHAR(50),
        shipping_charge DECIMAL(10,2) DEFAULT 0.00,
        date DATE,
        quantity DECIMAL(10,3),
        salesorder_number VARCHAR(100),
        sales_channel VARCHAR(50),
        created_time DATETIME,
        delivery_method VARCHAR(100),
        last_modified_time DATETIME,
        shipment_date DATE,
        is_carrier_shipment BOOLEAN DEFAULT FALSE,
        label_format VARCHAR(50),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
    """)

    with engine.connect() as conn:
        conn.execute(create_table_sql)
        conn.commit()

    print("✅ Table ready")


def clean_nan_values(df):
    """Replace NaN values with None for MySQL compatibility"""
    # List of columns that might contain NaN
    columns_to_clean = [
        'shipment_type', 'sales_channel', 'label_format',
        'tracking_number', 'delivery_method', 'shipment_id'
    ]

    for col in columns_to_clean:
        if col in df.columns:
            df[col] = df[col].replace({np.nan: None, 'nan': None, 'NaN': None, '': None})

    return df


def process_csv_and_insert(csv_content):
    """Process CSV and insert into MySQL"""

    print("🚀 Processing CSV data...")
    start_time = datetime.now()

    try:
        # 1. Read CSV from string content
        df = pd.read_csv(io.StringIO(csv_content))
        print(f"📊 Read {len(df)} records from CSV")

        # 2. Clean column names
        df.columns = df.columns.str.strip().str.lower()

        # 3. Get package IDs to delete
        package_ids = df['package_id'].astype(str).tolist()

        # 4. Connect to MySQL
        engine = create_mysql_connection()

        # 5. Create table if not exists
        create_table(engine)

        # 6. DELETE existing records
        with engine.connect() as conn:
            if package_ids:
                chunk_size = 100
                deleted_total = 0
                for i in range(0, len(package_ids), chunk_size):
                    chunk = package_ids[i:i + chunk_size]
                    placeholders = ','.join([':id' + str(j) for j in range(len(chunk))])
                    delete_sql = text(f"DELETE FROM packing_details WHERE package_id IN ({placeholders})")
                    params = {f'id{j}': chunk[j] for j in range(len(chunk))}
                    result = conn.execute(delete_sql, params)
                    deleted_total += result.rowcount
                conn.commit()
                print(f"🗑️ Deleted {deleted_total} existing records")

        # 7. CLEAN NAN VALUES - THIS FIXES THE ERROR
        df = clean_nan_values(df)

        # 8. Handle boolean columns
        if 'is_tracking_enabled' in df.columns:
            df['is_tracking_enabled'] = df['is_tracking_enabled'].map(
                {'true': 1, 'false': 0, True: 1, False: 0}).fillna(0)

        if 'is_carrier_shipment' in df.columns:
            df['is_carrier_shipment'] = df['is_carrier_shipment'].map(
                {'true': 1, 'false': 0, True: 1, False: 0}).fillna(0)

        # 9. Handle numeric columns
        df['shipping_charge'] = pd.to_numeric(df['shipping_charge'], errors='coerce').fillna(0)
        df['quantity'] = pd.to_numeric(df['quantity'], errors='coerce').fillna(0)

        # 10. Handle datetime columns
        datetime_cols = ['created_time', 'last_modified_time']
        for col in datetime_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce')

        # 11. Handle date columns
        date_cols = ['date', 'shipment_date']
        for col in date_cols:
            if col in df.columns:
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.date

        # 12. FINAL CLEANING - Replace any remaining NaN with None
        df = df.replace({np.nan: None})

        # 13. INSERT data - Using a simpler, more robust method
        inserted_count = 0
        with engine.begin() as conn:
            # Insert in chunks
            chunk_size = 50
            for i in range(0, len(df), chunk_size):
                chunk_df = df.iloc[i:i + chunk_size]

                # Convert to list of dictionaries and clean again
                records = chunk_df.to_dict('records')

                for record in records:
                    # Remove any keys with None values? No - keep them, MySQL accepts NULL
                    # But ensure no NaN remains
                    for key, value in record.items():
                        if isinstance(value, float) and pd.isna(value):
                            record[key] = None

                    # Build INSERT statement with all columns
                    columns = list(record.keys())
                    placeholders = ','.join([':' + col for col in columns])
                    insert_sql = text(f"""
                        INSERT INTO packing_details 
                        ({','.join(columns)}) 
                        VALUES ({placeholders})
                    """)

                    try:
                        conn.execute(insert_sql, record)
                        inserted_count += 1
                    except Exception as e:
                        print(f"⚠️ Error inserting record {inserted_count + 1}: {e}")
                        print(f"   Problematic record: {record}")
                        raise

                print(f"  📥 Inserted {inserted_count}/{len(df)} records...")

        # 14. Summary
        end_time = datetime.now()
        print(f"\n✅ SUCCESS!")
        print(f"   Total records processed: {len(df)}")
        print(f"   Records inserted: {inserted_count}")
        print(f"   Time taken: {(end_time - start_time).total_seconds():.2f} seconds")

        return len(df)

    except Exception as e:
        print(f"❌ Error processing data: {e}")
        raise


def verify_data():
    """Quick verification of inserted data"""
    try:
        engine = create_mysql_connection()

        with engine.connect() as conn:
            result = conn.execute(text("SELECT COUNT(*) as total FROM packing_details"))
            count = result.fetchone()[0]

            result = conn.execute(text("""
                SELECT 
                    status,
                    COUNT(*) as count,
                    SUM(quantity) as total_quantity
                FROM packing_details 
                GROUP BY status
            """))
            summary = result.fetchall()

            result = conn.execute(text("""
                SELECT date, COUNT(*) as packages, SUM(quantity) as quantity
                FROM packing_details 
                GROUP BY date 
                ORDER BY date DESC 
                LIMIT 5
            """))
            dates = result.fetchall()

        print("\n📋 DATABASE VERIFICATION:")
        print(f"   Total records in table: {count}")

        if summary:
            print("\n   Status breakdown:")
            for row in summary:
                print(f"     • {row[0] or 'Unknown'}: {row[1]} packages, {float(row[2]):.1f} quantity")

        if dates:
            print("\n   Latest dates:")
            for row in dates:
                print(f"     • {row[0]}: {row[1]} packages, {float(row[2]):.1f} quantity")

    except Exception as e:
        print(f"⚠️ Verification error: {e}")


# ============= MAIN EXECUTION =============
if __name__ == "__main__":
    print("=" * 60)
    print("📦 PACKING HISTORY CSV TO MYSQL IMPORTER")
    print("=" * 60)

    csv_content = fetch_csv_from_gmail()

    if csv_content is None:
        print("\n❌ Could not fetch CSV from Gmail")
    else:
        process_csv_and_insert(csv_content)
        verify_data()

    print("\n" + "=" * 60)
