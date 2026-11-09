import os
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
_tmp = tempfile.TemporaryDirectory()
os.environ['LOCALAPPDATA'] = _tmp.name

from PIL import Image
from PySide6.QtWidgets import QApplication, QFileDialog, QMessageBox, QDialog
from PySide6.QtCore import QRectF
from PySide6.QtGui import QPainter
from PySide6.QtPrintSupport import QPrinter
from openpyxl import load_workbook
import main

app = QApplication.instance() or QApplication([])


class QATest(unittest.TestCase):
    def setUp(self):
        self.window = main.Window()
        self.info = patch.object(QMessageBox, 'information', return_value=QMessageBox.Ok)
        self.question = patch.object(QMessageBox, 'question', return_value=QMessageBox.No)
        self.warning = patch.object(QMessageBox, 'warning', return_value=QMessageBox.Ok)
        self.info.start(); self.question.start(); self.warning.start()

    def tearDown(self):
        self.info.stop(); self.question.stop(); self.warning.stop()
        self.window.db.close(); self.window.close()
        db=main.connect()
        with db:
            db.execute('DELETE FROM movements'); db.execute('DELETE FROM products')
        db.close()

    def add(self, name='รถของเล่น'):
        self.window.product_name.setText(name)
        self.window.save_product()
        return self.window.db.execute('SELECT * FROM products ORDER BY id DESC LIMIT 1').fetchone()

    def test_create_scan_receive_sell_and_history(self):
        product = self.add()
        self.assertEqual(product['stock'], 0)
        self.window.scan.setText(product['barcode']); self.window.select_barcode()
        self.assertEqual(self.window.selected, product['id'])
        self.window.qty.setValue(3)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.window.apply_stock(1, 'รับเข้า')
        self.assertEqual(self.window.db.execute('SELECT stock FROM products').fetchone()[0], 3)
        self.assertEqual(self.window.qty.value(), 1)
        self.assertIn('รถของเล่น', self.window.detail.text())
        self.window.qty.setValue(2)
        with patch.object(QMessageBox, 'question', return_value=QMessageBox.Yes):
            self.window.apply_stock(-1, 'ขายหน้าร้าน')
        self.assertEqual(self.window.db.execute('SELECT stock FROM products').fetchone()[0], 1)
        self.assertEqual(self.window.qty.value(), 1)
        self.assertEqual(self.window.db.execute('SELECT count(*) FROM movements').fetchone()[0], 2)
        with self.assertRaises(ValueError): main.change_stock(self.window.db, product['id'], -2, 'ขาย')

    def test_history_is_grouped_by_day_and_keeps_expansion(self):
        item = self.add()
        with self.window.db:
            for stamp, delta in [('2026-11-05T09:00:00', 1),
                                 ('2026-11-06T08:00:00', 2),
                                 ('2026-11-06T10:00:00', -1)]:
                self.window.db.execute(
                    'INSERT INTO movements(product_id,delta,reason,created_at) VALUES(?,?,?,?)',
                    (item['id'], delta, 'ทดสอบ', stamp))
        self.window.refresh()
        tree = self.window.history_table
        self.assertEqual(tree.topLevelItemCount(), 2)
        latest = tree.topLevelItem(0)
        self.assertIn('2026-11-06', latest.text(0))
        self.assertIn('2 รายการ', latest.text(0))
        self.assertEqual(latest.child(0).text(0), '10:00:00')
        self.assertEqual(latest.child(0).text(2), '-1')
        self.assertTrue(latest.isExpanded())
        latest.setExpanded(False)
        self.window.refresh()
        self.assertFalse(tree.topLevelItem(0).isExpanded())

    def test_edit_button_updates_product_without_changing_stock(self):
        item=self.add()
        self.window.product_table.cellWidget(0,3).click()
        self.assertEqual(self.window.product_id,item['id'])
        self.window.product_name.setText('รถรุ่นใหม่')
        self.window.category.setText('รถ')
        self.window.product_barcode.setText('NEW001')
        self.window.save_product()
        updated=self.window.db.execute('SELECT * FROM products').fetchone()
        self.assertEqual((updated['name'],updated['category'],updated['barcode'],updated['stock']),('รถรุ่นใหม่','รถ','NEW001',0))

    def test_duplicate_barcode_and_unknown_scan(self):
        item = self.add('หนึ่ง'); self.assertTrue(item['barcode'].startswith('SP'))
        self.window.scan.setText('missing'); self.window.select_barcode()
        self.assertIsNone(self.window.selected)
        self.window.product_name.setText('สอง'); self.window.product_barcode.setText(item['barcode']); self.window.save_product()
        self.assertEqual(self.window.db.execute('SELECT count(*) FROM products').fetchone()[0], 1)

    def test_image_edit_export_and_backup(self):
        image = Path(_tmp.name)/'toy.png'; Image.new('RGB',(1200,800),'#90c9a3').save(image)
        self.window.image_file = str(image); item = self.add()
        self.assertTrue((main.IMAGES/item['image']).exists())
        self.window.scan.setText(item['barcode']); self.window.select_barcode()
        self.assertIsNotNone(self.window.photo.pixmap())
        self.assertGreater(self.window.photo.pixmap().width(), 220)
        with tempfile.TemporaryDirectory() as temp:
            xlsx=Path(temp)/'report.xlsx'; backup=Path(temp)/'backup.zip'
            with patch.object(QFileDialog,'getSaveFileName',side_effect=[(str(xlsx),'Excel'),(str(backup),'ZIP')]):
                self.window.export_xlsx(); self.window.backup()
            book=load_workbook(xlsx); self.assertEqual(book['สต๊อกปัจจุบัน']['A2'].value,item['barcode'])
            self.assertTrue(backup.is_file())
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(backup),'ZIP')), patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
                self.window.restore()
            self.assertEqual(self.window.db.execute('SELECT count(*) FROM products').fetchone()[0],1)

    def test_barcode_preview_uses_embedded_drawing(self):
        item=self.add()
        pixmap=main.barcode_pixmap(item['barcode'])
        self.assertGreater(pixmap.width(),400)
        self.assertFalse(pixmap.isNull())
        with patch.object(QDialog,'exec',return_value=QDialog.Rejected):
            self.window.print_label(item['barcode'])
        self.assertIn('บันทึกเสร็จสิ้น',self.window.product_status.text())

    def test_label_pdf_has_50_by_30_mm_page(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'label.pdf'
            printer = QPrinter(QPrinter.HighResolution)
            printer.setOutputFormat(QPrinter.PdfFormat)
            printer.setOutputFileName(str(path))
            main.configure_label_printer(printer)
            painter = QPainter(printer)
            self.assertTrue(painter.isActive())
            main.draw_barcode_label(painter, QRectF(printer.paperRect(QPrinter.DevicePixel)), 'SP0000000001')
            painter.end()
            data = path.read_bytes()
            box = re.search(rb'/MediaBox \[0 0 ([\d.]+) ([\d.]+)\]', data)
            self.assertIsNotNone(box)
            self.assertAlmostEqual(float(box.group(1)), 50 * 72 / 25.4, delta=0.5)
            self.assertAlmostEqual(float(box.group(2)), 30 * 72 / 25.4, delta=0.5)

    def test_logo_and_window_icon(self):
        self.assertFalse(self.window.windowIcon().isNull())
        self.assertTrue((main.ASSET_ROOT/'sp-toys-logo.png').is_file())
        logo = self.window.findChild(main.QLabel, 'brandIcon')
        self.assertEqual((logo.width(), logo.height()), (60, 60))
        self.assertEqual((logo.pixmap().width(), logo.pixmap().height()), (60, 60))
        self.assertEqual(logo.contentsRect().size(), logo.size())
        self.assertEqual(self.window.windowTitle(),'SP Toys · จัดการสต๊อก')

    def test_cancelled_stock_change_and_reopen(self):
        item=self.add()
        self.window.scan.setText(item['barcode']); self.window.select_barcode()
        self.window.qty.setValue(7)
        self.window.apply_stock(1,'รับเข้า')
        self.assertEqual(self.window.db.execute('SELECT stock FROM products').fetchone()[0],0)
        self.window.db.close()
        self.window.db=main.connect()
        self.assertEqual(self.window.db.execute('SELECT barcode FROM products').fetchone()[0],item['barcode'])

    def test_invalid_backup_rejected_without_overwriting(self):
        item=self.add()
        with tempfile.TemporaryDirectory() as temp:
            bad=Path(temp)/'bad.zip'
            with zipfile.ZipFile(bad,'w') as archive:
                archive.writestr('inventory.sqlite3',b'not a database')
            with patch.object(QFileDialog,'getOpenFileName',return_value=(str(bad),'ZIP')), patch.object(QMessageBox,'question',return_value=QMessageBox.Yes):
                self.window.restore()
        self.assertEqual(self.window.db.execute('SELECT barcode FROM products').fetchone()[0],item['barcode'])


if __name__ == '__main__': unittest.main()
