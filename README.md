# VoucherPro – Streamlit Voucher & Invoice App

VoucherPro is a small finance utility that lets you:

- Manage **vendors** and **accounts** (CRM-lite)
- Create and store **payment vouchers**
- Create and store **invoices**
- Attach supporting documents (PDF / images) to vouchers and invoices
- Generate a simple **voucher PDF** for download
- Control access with **user logins and roles** (admin / user)
- Manage user permissions (who can create vouchers, approve, manage users, etc.)
- Optionally browse the database with a basic **DB Browser** (admin only)

---

## 1. Project Structure

Recommended folder layout:

```text
voucherpro_app/
    app_main.py
    auth_module.py
    db_config.py
    crm_gateway.py
    vouchers_module.py
    invoices_module.py
    pdf_utils.py
    reporting_utils.py
    requirements.txt
    Dockerfile
    README.md
```

You already have each module as a `.txt` that you renamed to `.py`.

---

## 2. Environment Variables

The app uses **PostgreSQL**.  
Set this environment variable before running:

- `VOUCHER_DB_URL` – full Postgres connection string. Example:

On Windows (PowerShell):

```powershell
$env:VOUCHER_DB_URL="postgres://user:password@host:port/dbname?sslmode=require"
```

On macOS / Linux (bash):

```bash
export VOUCHER_DB_URL="postgres://user:password@host:port/dbname?sslmode=require"
```

The DSN must be valid for psycopg2.

---

## 3. Install Dependencies (local)

From inside the `voucherpro_app` folder:

```bash
python -m venv venv
# Windows
venv\Scripts\activate
# macOS / Linux
source venv/bin/activate

pip install -r requirements.txt
```

---

## 4. Initialize and Run the App

Once your environment variable and dependencies are set:

```bash
cd voucherpro_app
streamlit run app_main.py
```

The first run will:

- Create all necessary tables in your Postgres database:
  - `users`, `vouchers`, `voucher_lines`, `invoices`,
  - `vendors`, `accounts`, `audit_log`
- Show a login screen.

Because there is no user yet, you will need to **create the first admin user directly in the DB**:

```sql
INSERT INTO users (username, password_hash, role,
                   can_create_voucher, can_approve_voucher, can_manage_users)
VALUES (
  'admin',
  '<sha256_hash_of_password>',
  'admin',
  TRUE,
  TRUE,
  TRUE
);
```

To generate a hash in Python:

```python
import hashlib
print(hashlib.sha256("YourPassword123".encode("utf-8")).hexdigest())
```

Use that hash in the SQL.

After that, log in with the username/password you set.

---

## 5. Features & Pages

### 5.1 Vouchers

- Create payment vouchers with:
  - Voucher number
  - Vendor, requester, invoice number
  - Lines (description, amount, expense account, VAT%, WHT%)
  - Optional file attachment (PDF or image)
- Automatically calculates:
  - VAT value, WHT value, line totals
- View recent vouchers as a table
- Export a voucher to a simple PDF using `Download voucher PDF`

### 5.2 Invoices

- Create invoices with:
  - Invoice number, vendor invoice number
  - Vendor, summary
  - Vatable amount, VAT%, WHT%, non-vatable amount
  - Terms, currency, chart of account codes
  - Optional file attachment
- Automatically calculates:
  - VAT amount, WHT amount, subtotal, total amount
- View recent invoices as a table

### 5.3 CRM (Vendors & Accounts)

- Manage **vendors**:
  - Name
  - Contact person
  - Bank details
  - Notes
- Manage **accounts**:
  - Code
  - Name
  - Type: payable / expense / asset
- View existing vendors and accounts as tables

### 5.4 User Management

Visible only if the logged in user has `can_manage_users = TRUE`.

- Create new users:
  - Username
  - Password
  - Role: `user` or `admin`
  - Flags:
    - Can create vouchers
    - Can approve vouchers
    - Can manage users
- Edit existing users:
  - Change role and permission flags
- If you edit yourself, session permissions auto-refresh.

### 5.5 DB Browser (Admin Only)

Visible only if `role = 'admin'`.

- Simple SQL textarea to query the database:
  - Default query: `SELECT * FROM vouchers ORDER BY id DESC LIMIT 50;`
  - Shows results in a dataframe.
- Intended strictly for troubleshooting – not for regular users.

---

## 6. Roles and Permissions

Each user has:

- `role`:  
  - `"admin"` – full access + DB Browser  
  - `"user"` – restricted, controlled via flags.

Permission flags:

- `can_create_voucher` – can access *Vouchers*, *Invoices*, *CRM*.
- `can_approve_voucher` – placeholder for later workflow logic.
- `can_manage_users` – can access *User Management* and edit others.

When you create users from User Management:

- You can tick/untick each permission individually.

---

## 7. Docker Usage (Optional)

If you want to containerize the app:

1. Ensure `Dockerfile` exists in the root of `voucherpro_app`.
2. Build the image:

```bash
docker build -t voucherpro-app .
```

3. Run the container (example):

```bash
docker run -e VOUCHER_DB_URL="postgres://user:password@host:port/dbname?sslmode=require" -p 8501:8501 voucherpro-app
```

Then open:

- http://localhost:8501

The container image uses:

- Python base image
- Installs `requirements.txt`
- Runs `streamlit run app_main.py`

---

## 8. Notes & Next Steps

Possible future extensions:

- Add **approval workflow** for vouchers (status: draft / submitted / approved / rejected)
- Multi-currency enhancements for vouchers
- Better PDF templates (company logo, signatures, multi-page layout)
- Email integration to send voucher/invoice PDFs
- Attach multiple files per voucher/invoice (requires new table or storage approach)

For now, the app is intentionally simple and modular:

- Database logic in `db_config.py`
- Auth & access control in `auth_module.py`
- CRM in `crm_gateway.py`
- Voucher logic in `vouchers_module.py`
- Invoice logic in `invoices_module.py`
- PDF export in `pdf_utils.py`
- UI and navigation in `app_main.py`
