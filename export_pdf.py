import os, io, tempfile
from datetime import datetime
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas
from PyPDF2 import PdfReader, PdfWriter
import qrcode
import sqlite3
from encryption_utils import decrypt_privkey
from config import DB_PATH

def generate_pdf(records, out_path):
    packet = io.BytesIO()
    c = canvas.Canvas(packet, pagesize=A4)
    width, height = A4
    margin_x = 40
    y_start = height - 80

    for r in records:
        c.setFont('Helvetica-Bold', 12)
        c.drawString(margin_x, y_start, f"Address: {r['address']}")
        c.setFont('Helvetica', 10)
        # show only first/last parts of privkey for visual safety? We'll print full as user asked
        c.drawString(margin_x, y_start - 20, f"Private Key (hex): {r['privhex']}")
        c.drawString(margin_x, y_start - 40, f"Order ID: {r['order_id']}")
        c.drawString(margin_x, y_start - 60, f"Created: {r['created_at']}")
        # add QR
        qr = qrcode.make(r['address'])
        qr_path = tempfile.mktemp(suffix='.png')
        qr.save(qr_path)
        # Draw QR on right
        c.drawImage(qr_path, width - 160, y_start - 80, 120, 120)
        try:
            os.remove(qr_path)
        except:
            pass
        c.showPage()
    c.save()
    packet.seek(0)
    with open(out_path, 'wb') as f:
        f.write(packet.read())

def encrypt_pdf(input_path, output_path, password):
    reader = PdfReader(input_path)
    writer = PdfWriter()
    for p in reader.pages:
        writer.add_page(p)
    # password protection (owner/user same password)
    writer.encrypt(user_pwd=password, owner_pwd=None)
    with open(output_path, 'wb') as f:
        writer.write(f)

def export_all_keys_to_pdf(admin_id, output_filename_base, pdf_password):
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("SELECT address, priv_enc, order_id, created_at FROM links WHERE priv_enc IS NOT NULL")
    rows = cur.fetchall()
    records = []
    for address, priv_enc, order_id, created_at in rows:
        try:
            privhex = decrypt_privkey(priv_enc)
        except Exception as e:
            privhex = f"DECRYPT_ERROR: {str(e)}"
        records.append({
            'address': address,
            'privhex': privhex,
            'order_id': order_id,
            'created_at': created_at,
        })

    tmp_pdf = output_filename_base + '.tmp.pdf'
    final_pdf = output_filename_base + '.pdf'
    generate_pdf(records, tmp_pdf)
    encrypt_pdf(tmp_pdf, final_pdf, pdf_password)
    try:
        os.remove(tmp_pdf)
    except:
        pass

    # log the export
    cur.execute("INSERT INTO exports (admin_id, file_path, created_at) VALUES (?, ?, ?)",
                (admin_id, final_pdf, datetime.utcnow().isoformat()))
    conn.commit()
    conn.close()
    return final_pdf
