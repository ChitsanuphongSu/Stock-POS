import os
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from PIL import Image
from PySide6.QtCore import Qt, QRectF, QSizeF, QMarginsF
from PySide6.QtGui import QPixmap, QFont, QPainter, QColor, QIcon, QPageSize, QPageLayout
from PySide6.QtWidgets import (QApplication, QFileDialog, QFormLayout,
    QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox, QPushButton,
    QSpinBox, QDoubleSpinBox, QStackedWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
    QWidget, QHeaderView, QDialog, QTreeWidget, QTreeWidgetItem)
from barcode import Code128
from openpyxl import Workbook

ROOT = Path(os.getenv('LOCALAPPDATA', str(Path.home()))) / 'SP Toys'
DB = ROOT / 'inventory.sqlite3'
IMAGES = ROOT / 'images'
ROOT.mkdir(parents=True, exist_ok=True)
IMAGES.mkdir(exist_ok=True)
ASSET_ROOT = Path(getattr(sys, '_MEIPASS', Path(__file__).parent)) / 'assets'


def connect():
    db = sqlite3.connect(DB)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.executescript('''CREATE TABLE IF NOT EXISTS products (
      id INTEGER PRIMARY KEY, barcode TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
      category TEXT NOT NULL DEFAULT '', stock INTEGER NOT NULL DEFAULT 0 CHECK(stock>=0),
      low_stock INTEGER NOT NULL DEFAULT 3, image TEXT, active INTEGER NOT NULL DEFAULT 1,
      wholesale_price_satang INTEGER NOT NULL DEFAULT 0 CHECK(wholesale_price_satang>=0));
    CREATE TABLE IF NOT EXISTS movements (
      id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL REFERENCES products(id),
      delta INTEGER NOT NULL, reason TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value INTEGER NOT NULL);
    INSERT OR IGNORE INTO settings(key,value) VALUES('next_barcode',1);''')
    if 'wholesale_price_satang' not in {row['name'] for row in db.execute('PRAGMA table_info(products)')}:
        with db:
            db.execute('ALTER TABLE products ADD COLUMN wholesale_price_satang INTEGER NOT NULL DEFAULT 0 CHECK(wholesale_price_satang>=0)')
    return db


def change_stock(db, product_id, delta, reason):
    if not reason.strip() or not delta:
        raise ValueError('ระบุจำนวนและเหตุผล')
    with db:
        row = db.execute('SELECT stock FROM products WHERE id=?', (product_id,)).fetchone()
        if row is None or row['stock'] + delta < 0:
            raise ValueError('สินค้าไม่พบหรือสต๊อกไม่เพียงพอ')
        db.execute('UPDATE products SET stock=stock+? WHERE id=?', (delta, product_id))
        db.execute('INSERT INTO movements(product_id,delta,reason,created_at) VALUES(?,?,?,?)',
                   (product_id, delta, reason.strip(), datetime.now().isoformat(timespec='seconds')))


def next_barcode(db):
    number = db.execute("SELECT value FROM settings WHERE key='next_barcode'").fetchone()[0]
    while db.execute('SELECT 1 FROM products WHERE barcode=?', (f'SP{number:010d}',)).fetchone():
        number += 1
    return f'SP{number:010d}', number + 1


def barcode_pixmap(code):
    image = QPixmap(500, 300)
    image.fill(Qt.white)
    painter = QPainter(image)
    draw_barcode_label(painter, QRectF(image.rect()), code)
    painter.end()
    return image


def draw_barcode_label(painter, rect, code):
    """Draw a 50 × 30 mm label at any resolution, preserving barcode geometry."""
    bits = Code128(code).build()[0]
    margin = rect.width() * 0.06
    bar_width = (rect.width() - 2 * margin) / len(bits)
    top = rect.top() + rect.height() * 0.12
    bar_height = rect.height() * 0.56
    painter.fillRect(rect, Qt.white)
    for position, bit in enumerate(bits):
        if bit == '1':
            painter.fillRect(QRectF(rect.left() + margin + position * bar_width,
                                    top, bar_width, bar_height), Qt.black)
    font = QFont('Arial')
    font.setPixelSize(max(8, int(rect.height() * 0.12)))
    painter.setFont(font)
    painter.setPen(Qt.black)
    painter.drawText(QRectF(rect.left() + margin, rect.top() + rect.height() * 0.72,
                            rect.width() - 2 * margin, rect.height() * 0.18),
                     Qt.AlignCenter, code)


def configure_label_printer(printer):
    printer.setPageSize(QPageSize(QSizeF(50, 30), QPageSize.Millimeter, '50 x 30 mm'))
    printer.setPageMargins(QMarginsF(0, 0, 0, 0), QPageLayout.Millimeter)
    printer.setFullPage(True)


class ScanLineEdit(QLineEdit):
    """Replace the previous completed scan when the next barcode starts."""
    def __init__(self):
        super().__init__()
        self.replace_on_next_scan = False

    def keyPressEvent(self, event):
        if (self.replace_on_next_scan and event.text().isprintable()
                and not event.modifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier)):
            self.clear()
            self.replace_on_next_scan = False
        super().keyPressEvent(event)


class Window(QMainWindow):
    def __init__(self):
        super().__init__()
        self.db = connect()
        self.setWindowTitle('SP Toys · จัดการสต๊อก')
        self.setWindowIcon(QIcon(str(ASSET_ROOT / 'sp-toys.ico')))
        self.resize(1100, 720)
        self.selected = None
        self.pages = QStackedWidget()
        shell = QWidget(); shell.setObjectName('shell'); outer = QHBoxLayout(shell); outer.setContentsMargins(0,0,0,0); outer.setSpacing(0)
        side = QWidget(); side.setObjectName('sidebar'); side.setFixedWidth(220)
        sidebar = QVBoxLayout(side); sidebar.setContentsMargins(18,24,18,20); sidebar.setSpacing(8)
        brand_row = QHBoxLayout()
        brand_icon = QLabel(); brand_icon.setObjectName('brandIcon')
        brand_icon.setPixmap(QPixmap(str(ASSET_ROOT / 'sp-toys-logo.png')).scaled(60, 60, Qt.KeepAspectRatio, Qt.SmoothTransformation))
        brand_icon.setFixedSize(60,60); brand_row.addWidget(brand_icon)
        title = QLabel('SP Toys'); title.setObjectName('brand'); brand_row.addWidget(title)
        sidebar.addLayout(brand_row)
        subtitle = QLabel('จัดการสต๊อกร้านของเล่น'); subtitle.setObjectName('subtitle'); sidebar.addWidget(subtitle)
        sidebar.addSpacing(32)
        self.nav_buttons = []
        for i, label in enumerate(['จัดการสต๊อก', 'สินค้า', 'ประวัติ', 'ส่งออก / สำรอง']):
            button = QPushButton(label); button.setObjectName('nav'); button.clicked.connect(lambda _, n=i: self.show_page(n)); sidebar.addWidget(button); self.nav_buttons.append(button)
        sidebar.addStretch(); outer.addWidget(side); outer.addWidget(self.pages, 1)
        self.setCentralWidget(shell)
        self.build_stock(); self.build_products(); self.build_history(); self.build_export(); self.refresh(); self.show_page(0)
        self.setStyleSheet('''QWidget{font-family:"Leelawadee UI";font-size:14px;background:#f6f9f7;color:#243b30}
          QWidget#sidebar{background:#ffffff;border-right:1px solid #e1ece5}
          QLabel#brandIcon{padding:0;background:#ffffff;border:0}
          QLabel#brand{font-size:26px;font-weight:700;color:#306a49;background:#ffffff;padding:6px}
          QLabel#subtitle{font-size:11px;color:#617f6c;background:#ffffff;padding-left:8px}
          QPushButton{background:#4f9167;color:#ffffff;border:0;border-radius:10px;padding:12px 17px;font-weight:600}
          QPushButton:hover{background:#36764f} QPushButton#nav{background:#ffffff;color:#365c43;text-align:left}
          QPushButton#nav:hover{background:#eaf4ed} QPushButton#nav[active="true"]{background:#deefe3;color:#285b3b}
          QPushButton#edit{background:#e2f0e7;color:#285b3b;padding:7px 12px;min-width:42px}
          QPushButton#edit:hover{background:#cde5d4}
          QLineEdit,QSpinBox,QDoubleSpinBox,QTableWidget{background:#ffffff;border:1px solid #dbe9df;border-radius:9px;padding:9px;selection-background-color:#dceee2}
          QLineEdit:focus,QSpinBox:focus,QDoubleSpinBox:focus{border:2px solid #8fc3a2}
          QHeaderView::section{background:#eef6f0;color:#5e7a68;border:0;padding:10px;font-weight:600}
          QTableWidget{gridline-color:#edf3ee} QLabel{padding:4px}''')

    def page(self, heading):
        widget = QWidget(); layout = QVBoxLayout(widget); layout.setContentsMargins(32, 30, 32, 30); layout.setSpacing(15)
        label = QLabel(heading); label.setStyleSheet('font-size:27px;font-weight:650;color:#416f55'); layout.addWidget(label)
        self.pages.addWidget(widget); return layout

    def build_stock(self):
        box = self.page('จัดการสต๊อก')
        self.scan = ScanLineEdit(); self.scan.setPlaceholderText('สแกนบาร์โค้ดหรือพิมพ์รหัส แล้วกด Enter')
        self.scan.returnPressed.connect(self.select_barcode); box.addWidget(self.scan)
        self.photo = QLabel('ไม่มีรูปสินค้า'); self.photo.setMinimumSize(360, 270); self.photo.setMaximumHeight(300); self.photo.setAlignment(Qt.AlignCenter)
        self.photo.setStyleSheet('background:#edf5ef;border:1px solid #deeadf;border-radius:14px;color:#8ba998')
        box.addWidget(self.photo, alignment=Qt.AlignHCenter)
        self.detail = QLabel('สแกนเพื่อเลือกสินค้า'); self.detail.setAlignment(Qt.AlignCenter); self.detail.setStyleSheet('font-size:22px;font-weight:600;color:#456c53'); box.addWidget(self.detail)
        self.category_detail = QLabel(''); self.category_detail.setAlignment(Qt.AlignCenter); self.category_detail.setStyleSheet('font-size:14px;color:#658272'); box.addWidget(self.category_detail)
        self.price_detail = QLabel(''); self.price_detail.setAlignment(Qt.AlignCenter); self.price_detail.setStyleSheet('font-size:17px;font-weight:600;color:#306a49'); box.addWidget(self.price_detail)
        self.qty = QSpinBox(); self.qty.setRange(1, 1000000); box.addWidget(self.qty)
        actions = QHBoxLayout()
        for label, direction, reason in [('รับสินค้าเข้า', 1, 'รับเข้า'), ('ตัดสินค้าที่ขาย', -1, 'ขายหน้าร้าน')]:
            b = QPushButton(label); b.clicked.connect(lambda _, d=direction, r=reason: self.apply_stock(d, r)); actions.addWidget(b)
        box.addLayout(actions)
        adjust = QPushButton('ปรับยอดนับจริง'); adjust.clicked.connect(self.adjust); box.addWidget(adjust)
        self.stock_table = QTableWidget(0, 5); self.stock_table.setHorizontalHeaderLabels(['บาร์โค้ด', 'สินค้า', 'หมวดหมู่', 'ราคาขายส่ง', 'คงเหลือ']); self.stock_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch); box.addWidget(self.stock_table)

    def build_products(self):
        box = self.page('สินค้า')
        self.product_barcode = QLineEdit(); self.product_name = QLineEdit(); self.category = QLineEdit()
        self.low = QSpinBox(); self.low.setValue(3)
        self.wholesale_price = QDoubleSpinBox(); self.wholesale_price.setDecimals(2); self.wholesale_price.setRange(0, 999999999.99); self.wholesale_price.setSingleStep(1); self.wholesale_price.setPrefix('฿')
        self.image_file = None; self.product_id = None
        self.saved_barcode = None
        self.product_status = QLabel('กรอกข้อมูลสินค้าแล้วกดบันทึก'); self.product_status.setStyleSheet('color:#39734e;font-weight:600')
        form = QFormLayout()
        for name, field in [('บาร์โค้ด (เว้นว่างเพื่อสร้างอัตโนมัติ)', self.product_barcode), ('ชื่อสินค้า', self.product_name), ('หมวดหมู่', self.category), ('ราคาขายส่ง (บาท)', self.wholesale_price), ('แจ้งเตือนเมื่อเหลือ', self.low)]: form.addRow(name, field)
        box.addLayout(form)
        box.addWidget(QLabel('ขั้นตอน: บันทึกสินค้า → พิมพ์ฉลากแปะสินค้า → สแกนที่หน้าจัดการสต๊อก → ระบุจำนวน → ยืนยันรับเข้า'))
        buttons = QHBoxLayout()
        for label, method in [('สร้างบาร์โค้ด', self.generate_code), ('เลือกรูป', self.choose_image), ('บันทึกสินค้า', self.save_product), ('พิมพ์ฉลาก', self.print_label)]:
            b = QPushButton(label); b.clicked.connect(method); buttons.addWidget(b)
        box.addLayout(buttons)
        box.addWidget(self.product_status)
        self.product_table = QTableWidget(0, 5); self.product_table.setHorizontalHeaderLabels(['บาร์โค้ด', 'ชื่อ', 'ราคาขายส่ง', 'คงเหลือ', 'แก้ไข']); self.product_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.product_table.cellDoubleClicked.connect(self.edit_product); box.addWidget(self.product_table)

    def build_history(self):
        box = self.page('ประวัติการเคลื่อนไหว')
        self.history_table = QTreeWidget()
        self.history_table.setHeaderLabels(['วัน / เวลา', 'สินค้า', 'จำนวน', 'เหตุผล'])
        self.history_table.header().setSectionResizeMode(QHeaderView.Stretch)
        self.history_table.setRootIsDecorated(True)
        self.history_table.setAlternatingRowColors(True)
        self.history_table.setStyleSheet('QTreeWidget::item{min-height:32px} QTreeWidget::item:!selected{color:#243b30}')
        box.addWidget(self.history_table)

    def build_export(self):
        box = self.page('ส่งออกและสำรองข้อมูล')
        for label, method in [('ส่งออก .xlsx', self.export_xlsx), ('สำรองฐานข้อมูลและรูป', self.backup), ('กู้คืนไฟล์สำรอง', self.restore)]:
            b = QPushButton(label); b.clicked.connect(method); box.addWidget(b)
        box.addStretch()

    def show_page(self, i):
        self.pages.setCurrentIndex(i); self.refresh()
        for n, button in enumerate(self.nav_buttons):
            button.setProperty('active', n == i); button.style().unpolish(button); button.style().polish(button)
        if i == 0: self.scan.setFocus()

    def error(self, exc): QMessageBox.warning(self, 'SP Toys', str(exc))

    def select_barcode(self):
        row = self.db.execute('SELECT * FROM products WHERE barcode=? AND active=1', (self.scan.text().strip(),)).fetchone()
        if not row:
            self.selected = None; self.detail.setText('ไม่พบสินค้า'); self.category_detail.clear(); self.price_detail.clear()
            self.photo.clear(); self.photo.setText('ไม่มีรูปสินค้า')
            self.scan.replace_on_next_scan = True; self.scan.setFocus(); self.scan.selectAll()
            return
        self.selected = row['id']; self.detail.setText(f"{row['name']}  ·  คงเหลือ {row['stock']} ชิ้น")
        self.category_detail.setText(f"หมวดหมู่: {row['category'] or 'ไม่ระบุ'}   |   บาร์โค้ด: {row['barcode']}")
        self.price_detail.setText(f"ราคาขายส่ง: ฿{row['wholesale_price_satang'] / 100:,.2f}")
        path = IMAGES / row['image'] if row['image'] else None
        self.photo.setPixmap(QPixmap(str(path)).scaled(355, 265, Qt.KeepAspectRatio, Qt.SmoothTransformation) if path and path.exists() else QPixmap())
        if not path or not path.exists(): self.photo.setText('ไม่มีรูปสินค้า')
        self.scan.replace_on_next_scan = True; self.scan.setFocus(); self.scan.selectAll()

    def apply_stock(self, direction, reason):
        try:
            if self.selected is None: raise ValueError('เลือกสินค้าก่อน')
            count = self.qty.value()
            if QMessageBox.question(self, 'ยืนยัน', f'{reason} {count} ชิ้น?') != QMessageBox.Yes: return
            change_stock(self.db, self.selected, direction * count, reason); self.qty.setValue(1); self.refresh(); self.select_barcode()
        except Exception as exc: self.error(exc)

    def adjust(self):
        from PySide6.QtWidgets import QInputDialog
        if self.selected is None: return self.error('เลือกสินค้าก่อน')
        row = self.db.execute('SELECT stock FROM products WHERE id=?', (self.selected,)).fetchone()
        amount, ok = QInputDialog.getInt(self, 'ปรับยอดนับจริง', 'จำนวนที่นับได้', row['stock'], 0, 1000000)
        if not ok or amount == row['stock']: return
        reason, ok = QInputDialog.getText(self, 'เหตุผล', 'เหตุผลการปรับ')
        if ok:
            try: change_stock(self.db, self.selected, amount-row['stock'], reason); self.refresh(); self.select_barcode()
            except Exception as exc: self.error(exc)

    def generate_code(self):
        self.product_barcode.setText(next_barcode(self.db)[0])
        self.product_status.setText('สร้างรหัสแล้ว · บันทึกสินค้าก่อนพิมพ์ฉลาก')

    def choose_image(self):
        path, _ = QFileDialog.getOpenFileName(self, 'เลือกรูปสินค้า', '', 'Images (*.jpg *.jpeg *.png)')
        if path:
            self.image_file = path
            self.product_status.setText(f'เลือกรูป {Path(path).name} แล้ว · กดบันทึกสินค้าเพื่อยืนยัน')

    def save_product(self):
        try:
            code, name = self.product_barcode.text().strip(), self.product_name.text().strip()
            if not name: raise ValueError('กรอกชื่อสินค้า')
            if not code:
                self.generate_code()
                code = self.product_barcode.text().strip()
            price_satang = int(round(self.wholesale_price.value() * 100))
            with self.db:
                if self.product_id:
                    self.db.execute('UPDATE products SET barcode=?,name=?,category=?,low_stock=?,wholesale_price_satang=? WHERE id=?', (code,name,self.category.text(),self.low.value(),price_satang,self.product_id))
                    pid = self.product_id
                else:
                    generated, next_number = next_barcode(self.db)
                    if code == generated:
                        self.db.execute("UPDATE settings SET value=? WHERE key='next_barcode'", (next_number,))
                    cur = self.db.execute('INSERT INTO products(barcode,name,category,stock,low_stock,wholesale_price_satang) VALUES(?,?,?,?,?,?)', (code,name,self.category.text(),0,self.low.value(),price_satang))
                    pid = cur.lastrowid
                if self.image_file:
                    filename = f'{pid}.jpg'
                    image = Image.open(self.image_file).convert('RGB'); image.thumbnail((900,900)); image.save(IMAGES/filename, quality=85)
                    self.db.execute('UPDATE products SET image=? WHERE id=?', (filename,pid))
            self.saved_barcode = code
            self.product_status.setText(f'บันทึกเสร็จสิ้น · {name} · บาร์โค้ด {code}')
            self.product_id = None; self.image_file = None; self.product_barcode.clear(); self.product_name.clear(); self.category.clear(); self.wholesale_price.setValue(0); self.refresh()
            if QMessageBox.question(self, 'บันทึกสินค้าแล้ว', f'รหัส {code} บันทึกแล้ว\nต้องการพิมพ์ฉลากบาร์โค้ดตอนนี้หรือไม่?') == QMessageBox.Yes:
                self.print_label(code)
            self.show_page(0)
            self.scan.setFocus()
        except Exception as exc: self.error(exc)

    def edit_product(self, row, _):
        code = self.product_table.item(row,0).text(); item = self.db.execute('SELECT * FROM products WHERE barcode=?',(code,)).fetchone()
        if not item: return
        self.product_id = item['id']; self.product_barcode.setText(code); self.product_name.setText(item['name']); self.category.setText(item['category']); self.low.setValue(item['low_stock']); self.wholesale_price.setValue(item['wholesale_price_satang'] / 100)
        self.image_file = None
        self.product_status.setText(f'กำลังแก้ไข {item["name"]} · กดบันทึกเพื่อยืนยัน')
        self.show_page(1)
        self.product_name.setFocus()

    def print_label(self, code=None):
        if isinstance(code, bool): code = None
        code = code or self.product_barcode.text().strip() or self.saved_barcode
        if not code: return self.error('กรอกหรือสร้างบาร์โค้ดก่อน')
        from PySide6.QtPrintSupport import QPrinter, QPrintDialog
        if not self.db.execute('SELECT 1 FROM products WHERE barcode=?', (code,)).fetchone():
            return self.error('บันทึกสินค้าก่อนพิมพ์ฉลาก')
        try: image = barcode_pixmap(code)
        except Exception as exc: return self.error(f'รหัสบาร์โค้ดไม่รองรับ: {exc}')
        preview = QDialog(self); preview.setWindowTitle('ตัวอย่างบาร์โค้ด · '+code); preview.setMinimumWidth(560)
        layout = QVBoxLayout(preview)
        layout.addWidget(QLabel('ตัวอย่างฉลาก 50 × 30 มม. · ตั้งขนาดกระดาษในไดรเวอร์ให้ตรงกัน'))
        picture = QLabel(); picture.setAlignment(Qt.AlignCenter); picture.setPixmap(image); layout.addWidget(picture)
        buttons = QHBoxLayout(); print_button = QPushButton('พิมพ์ฉลาก'); close_button = QPushButton('ปิด')
        buttons.addWidget(print_button); buttons.addWidget(close_button); layout.addLayout(buttons)
        close_button.clicked.connect(preview.reject)
        def do_print():
            printer = QPrinter(QPrinter.HighResolution); configure_label_printer(printer)
            dialog = QPrintDialog(printer, preview)
            if dialog.exec():
                configure_label_printer(printer)
                painter = QPainter(printer)
                area = printer.paperRect(QPrinter.DevicePixel)
                draw_barcode_label(painter, QRectF(area), code)
                painter.end(); self.product_status.setText(f'ส่งฉลาก {code} ไปเครื่องพิมพ์แล้ว'); preview.accept()
        print_button.clicked.connect(do_print)
        preview.exec()

    def refresh(self):
        rows = self.db.execute('SELECT * FROM products WHERE active=1 ORDER BY name').fetchall()
        self.stock_table.setRowCount(len(rows)); self.product_table.setRowCount(len(rows))
        for i, r in enumerate(rows):
            price = f"฿{r['wholesale_price_satang'] / 100:,.2f}"
            for table, values in [(self.stock_table,[r['barcode'],r['name'],r['category'],price,str(r['stock'])]),(self.product_table,[r['barcode'],r['name'],price,str(r['stock'])])]:
                for j, value in enumerate(values): table.setItem(i,j,QTableWidgetItem(value))
            edit = QPushButton('✎'); edit.setObjectName('edit'); edit.setToolTip(f"แก้ไข {r['name']}")
            edit.clicked.connect(lambda _, row=i: self.edit_product(row, 0))
            self.product_table.setCellWidget(i,4,edit)
        expanded = {self.history_table.topLevelItem(i).data(0, Qt.UserRole): self.history_table.topLevelItem(i).isExpanded()
                    for i in range(self.history_table.topLevelItemCount())}
        history = self.db.execute('SELECT m.created_at,p.name,m.delta,m.reason FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.created_at DESC,m.id DESC').fetchall()
        self.history_table.clear()
        groups = {}
        for r in history:
            day = r['created_at'][:10]
            if day not in groups:
                group = QTreeWidgetItem(self.history_table, [day, '', '', ''])
                group.setData(0, Qt.UserRole, day)
                group.setExpanded(expanded.get(day, True))
                for col in range(4):
                    group.setBackground(col, QColor('#deefe3'))
                    group.setForeground(col, QColor('#285b3b'))
                    font = group.font(col); font.setBold(True); group.setFont(col, font)
                groups[day] = group
            group = groups[day]
            QTreeWidgetItem(group, [r['created_at'][11:19], r['name'],
                                    f'{r["delta"]:+d}', r['reason']])
            group.setText(0, f'{day}  ·  {group.childCount()} รายการ')

    def export_xlsx(self):
        path, _ = QFileDialog.getSaveFileName(self, 'ส่งออก Excel', 'SP-Toys-Stock.xlsx', 'Excel (*.xlsx)')
        if not path: return
        try:
            book = Workbook(); stock = book.active; stock.title='สต๊อกปัจจุบัน'; stock.append(['บาร์โค้ด','สินค้า','หมวดหมู่','ราคาขายส่ง (บาท)','คงเหลือ','แจ้งเตือนเมื่อเหลือ'])
            def safe(value):
                return "'"+value if isinstance(value,str) and value.startswith(('=','+','-','@')) else value
            for r in self.db.execute('SELECT barcode,name,category,wholesale_price_satang,stock,low_stock FROM products ORDER BY name'):
                stock.append([safe(r['barcode']), safe(r['name']), safe(r['category']), r['wholesale_price_satang'] / 100, r['stock'], r['low_stock']])
                stock.cell(stock.max_row, 4).number_format = '#,##0.00'
            history = book.create_sheet('ประวัติ'); history.append(['วันเวลา','บาร์โค้ด','สินค้า','จำนวน','เหตุผล'])
            for r in self.db.execute('SELECT m.created_at,p.barcode,p.name,m.delta,m.reason FROM movements m JOIN products p ON p.id=m.product_id ORDER BY m.id'): history.append([safe(v) for v in r])
            for sheet in book:
                sheet.freeze_panes='A2'; sheet.auto_filter.ref=sheet.dimensions
                for column in sheet.columns:
                    letter=column[0].column_letter; sheet.column_dimensions[letter].width=24
            book.save(path); QMessageBox.information(self,'SP Toys','ส่งออกไฟล์แล้ว')
        except Exception as exc: self.error(exc)

    def backup(self):
        path, _ = QFileDialog.getSaveFileName(self,'สำรองข้อมูล','SP-Toys-Backup.zip','ZIP (*.zip)')
        if not path: return
        try:
            with tempfile.TemporaryDirectory() as temp:
                saved=Path(temp)/'inventory.sqlite3'; target=sqlite3.connect(saved); self.db.backup(target); target.close()
                with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
                    archive.write(saved,'inventory.sqlite3')
                    for image in IMAGES.glob('*.jpg'): archive.write(image,'images/'+image.name)
            QMessageBox.information(self,'SP Toys','สำรองข้อมูลแล้ว')
        except Exception as exc: self.error(exc)

    def restore(self):
        path, _ = QFileDialog.getOpenFileName(self,'เลือกไฟล์สำรอง','','ZIP (*.zip)')
        if not path or QMessageBox.question(self,'ยืนยัน','กู้คืนจะเขียนทับข้อมูลปัจจุบัน ต้องการดำเนินการ?') != QMessageBox.Yes: return
        try:
            with zipfile.ZipFile(path) as archive, tempfile.TemporaryDirectory() as temp:
                if 'inventory.sqlite3' not in archive.namelist(): raise ValueError('ไม่มีฐานข้อมูลในไฟล์สำรอง')
                for member in archive.namelist():
                    if member != 'inventory.sqlite3' and not (member.startswith('images/') and member.endswith('.jpg') and '/' not in member[7:]): raise ValueError('ไฟล์สำรองไม่ถูกต้อง')
                archive.extractall(temp)
                source_path = Path(temp)/'inventory.sqlite3'
                source=sqlite3.connect(source_path)
                try:
                    if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok': raise ValueError('ฐานข้อมูลเสียหาย')
                    tables={r[0] for r in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                    if not {'products','movements'}.issubset(tables): raise ValueError('รูปแบบฐานข้อมูลไม่ถูกต้อง')
                finally: source.close()
                staged_images=Path(temp)/'images'
                check=sqlite3.connect(source_path)
                try:
                    for r in check.execute('SELECT image FROM products WHERE image IS NOT NULL'):
                        name = r[0]
                        if Path(name).name != name or not name.endswith('.jpg') or not (staged_images/name).is_file():
                            raise ValueError('รูปสินค้าในไฟล์สำรองไม่ครบหรือชื่อไม่ถูกต้อง')
                finally: check.close()
                safety=ROOT/'before-restore.sqlite3'
                target=sqlite3.connect(safety)
                try: self.db.backup(target)
                finally: target.close()
                old_images=Path(temp)/'old-images'
                shutil.copytree(IMAGES, old_images)
                self.db.close()
                try:
                    shutil.copy2(source_path,DB)
                    for image in IMAGES.glob('*.jpg'): image.unlink()
                    for image in staged_images.glob('*.jpg'):
                        shutil.copy2(image,IMAGES/image.name)
                    self.db=connect(); self.refresh()
                except Exception:
                    if getattr(self, 'db', None): self.db.close()
                    shutil.copy2(safety,DB)
                    for image in IMAGES.glob('*.jpg'): image.unlink()
                    for image in old_images.glob('*.jpg'): shutil.copy2(image,IMAGES/image.name)
                    self.db=connect(); raise
                QMessageBox.information(self,'SP Toys','กู้คืนแล้ว')
        except Exception as exc: self.error(exc)


if __name__ == '__main__':
    app=QApplication(sys.argv); app.setFont(QFont('Leelawadee UI', 10)); window=Window(); window.show(); sys.exit(app.exec())
