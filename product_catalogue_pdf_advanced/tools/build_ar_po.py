#!/usr/bin/env python3
"""Regenerate ar.po from the exported .pot and the glossary below.

Usage (from the module folder):
    odoo-bin ... --i18n-export=i18n/product_catalogue_pdf_advanced.pot \
        --modules=product_catalogue_pdf_advanced --stop-after-init
    python3 tools/build_ar_po.py

Keeping the Arabic glossary in code makes it reviewable in diffs and lets
the test suite assert that every printed label is translated.
"""
import os
import sys

import polib

HERE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "i18n")

AR = {
    # --- Printed on the PDF -------------------------------------------------
    "<span>Prices in</span>": "<span>الأسعار بـ</span>",
    "<span>tax excluded</span>": "<span>غير شاملة الضريبة</span>",
    "<span>tax included</span>": "<span>شاملة الضريبة</span>",
    "<span>Availability:</span>": "<span>التوفر:</span>",
    "<span>Generated</span>": "<span>أُعدّ في</span>",
    "· <span>Valid until</span>": "· <span>صالح حتى</span>",
    "<span>in</span>": "<span>في</span>",
    "<span>categories</span>": "<span>فئات</span>",
    "<span>→</span>": "<span>←</span>",
    "Contents": "المحتويات",
    "continued": "تابع",
    "From": "ابتداءً من",
    "In stock": "متوفر",
    "Low stock": "كمية محدودة",
    "On request": "عند الطلب",
    "In this edition": "في هذا الإصدار",
    "Products": "المنتجات",
    "Edition": "الإصدار",
    "Pricelist": "قائمة الأسعار",
    "Prices": "الأسعار",
    "Valid from": "صالح من",
    "Valid until": "صالح حتى",
    "Logo": "الشعار",
    "QR": "رمز QR",
    "General range": "التشكيلة العامة",
    "Customer-specific": "خاص بالعميل",
    "Seasonal / Promotion": "موسمي / عروض",
    "Internal / Sales team": "داخلي / فريق المبيعات",
    # --- Email -------------------------------------------------------------
    "{{ object.company_id.name }} – {{ object.cover_title or object.name }}":
        "{{ object.company_id.name }} – {{ object.cover_title or object.name }}",
    # --- Back-office ---------------------------------------------------------
    "Product Catalogue": "كتالوج المنتجات",
    "Product Catalogues": "كتالوجات المنتجات",
    "Product Catalogue PDF": "ملف PDF لكتالوج المنتجات",
    "Product Catalogue: Send by email": "كتالوج المنتجات: إرسال بالبريد الإلكتروني",
    "Catalogues": "الكتالوجات",
    "Catalog": "الكتالوج",
    "Catalogue Type": "نوع الكتالوج",
    "Configuration": "الإعدادات",
    "Email Templates": "قوالب البريد الإلكتروني",
    "Email Template": "قالب البريد الإلكتروني",
    "Print PDF": "طباعة PDF",
    "Send by Email": "إرسال بالبريد الإلكتروني",
    "Preview": "معاينة",
    "Send": "إرسال",
    "Cancel": "إلغاء",
    "Send Catalogue": "إرسال الكتالوج",
    "Send Product Catalogue": "إرسال كتالوج المنتجات",
    "Recipients": "المستلمون",
    "Language": "اللغة",
    "Use Each Recipient's Pricelist": "استخدام قائمة أسعار كل مستلم",
    "Use Each Recipient's Language": "استخدام لغة كل مستلم",
    "Catalogue emailed:": "تم إرسال الكتالوج:",
    "Add customers...": "أضف عملاء...",
    "Design": "التصميم",
    "Content": "المحتوى",
    "Delivery": "الإرسال",
    "Layout": "التخطيط",
    "Paper": "الورق",
    "Style": "النمط",
    "Grid cards": "بطاقات شبكية",
    "Compact list": "قائمة مدمجة",
    "Showcase": "عرض مميز",
    "Boxes per Row": "عدد المربعات في الصف",
    "Image Size": "حجم الصورة",
    "Small": "صغير",
    "Medium": "متوسط",
    "Large": "كبير",
    "Print Quality": "جودة الطباعة",
    "Screen (lightest file)": "شاشة (أخف ملف)",
    "Office printer": "طابعة مكتبية",
    "Professional print": "طباعة احترافية",
    "Paper Size": "حجم الورق",
    "A4": "A4",
    "US Letter": "Letter أمريكي",
    "Orientation": "الاتجاه",
    "Portrait": "عمودي",
    "Landscape": "أفقي",
    "Accent Color": "اللون المميز",
    "Cover Page": "صفحة الغلاف",
    "Cover Title": "عنوان الغلاف",
    "Cover Subtitle": "العنوان الفرعي للغلاف",
    "Cover Image": "صورة الغلاف",
    "Table of Contents": "جدول المحتويات",
    "Cover & navigation": "الغلاف والتنقل",
    "Page breaks": "فواصل الصفحات",
    "Group By Category": "تجميع حسب الفئة",
    "New Page per Category": "صفحة جديدة لكل فئة",
    "Max Rows Per Page": "الحد الأقصى للصفوف في الصفحة",
    "Footer Note": "ملاحظة التذييل",
    "Show": "إظهار",
    "Show Image": "إظهار الصورة",
    "Show Price": "إظهار السعر",
    "Show Price Before Discount": "إظهار السعر قبل الخصم",
    "Show Description": "إظهار الوصف",
    "Description Source": "مصدر الوصف",
    "Sales description": "وصف المبيعات",
    "Product description": "وصف المنتج",
    "Show Product Link (QR)": "إظهار رابط المنتج (QR)",
    "Product URL Pattern": "نمط رابط المنتج",
    "Show Internal Reference": "إظهار المرجع الداخلي",
    "Show Availability": "إظهار التوفر",
    "Links & availability": "الروابط والتوفر",
    "Tax excluded": "غير شامل الضريبة",
    "Tax included": "شامل الضريبة",
    "Warehouse": "المستودع",
    "Low Stock Threshold": "حد الكمية المحدودة",
    "Hide Unavailable Products": "إخفاء المنتجات غير المتوفرة",
    "Categories": "الفئات",
    "Product Tags": "وسوم المنتجات",
    "Attribute Values": "قيم الخصائص",
    "Only These Products": "هذه المنتجات فقط",
    "Excluded Products": "المنتجات المستبعدة",
    "Can be Sold Only": "القابلة للبيع فقط",
    "Advanced Filter": "عامل تصفية متقدم",
    "Advanced filter": "عامل تصفية متقدم",
    "Sort Order": "ترتيب الفرز",
    "Name": "الاسم",
    "Internal reference": "المرجع الداخلي",
    "Price: low to high": "السعر: من الأقل إلى الأعلى",
    "Price: high to low": "السعر: من الأعلى إلى الأقل",
    "Max Products": "الحد الأقصى للمنتجات",
    "Reuse Generated PDF For (min)": "إعادة استخدام ملف PDF لمدة (دقيقة)",
    "Product Count": "عدد المنتجات",
    "Last Generated On": "آخر إنشاء في",
    "Selection": "الاختيار",
    "Order & limits": "الترتيب والحدود",
    "Explicit products": "منتجات محددة",
    "Validity": "الصلاحية",
    "Valid From": "صالح من",
    "Valid Until": "صالح حتى",
    "Until": "حتى",
    "Promotions": "العروض",
    "Type": "النوع",
    "Group By": "تجميع حسب",
    "Products in %s": "المنتجات في %s",
    "%s (copy)": "%s (نسخة)",
    "User": "مستخدم",
    "Administrator": "مدير",
    "Can view, print and email product catalogues.":
        "يمكنه عرض كتالوجات المنتجات وطباعتها وإرسالها بالبريد الإلكتروني.",
    "Can create and configure product catalogues.": "يمكنه إنشاء كتالوجات المنتجات وإعدادها.",
    "Design, print and send PDF product catalogues.":
        "تصميم كتالوجات المنتجات بصيغة PDF وطباعتها وإرسالها.",
    "Create your first product catalogue": "أنشئ أول كتالوج منتجات",
    "e.g. Spring 2027 Range": "مثال: تشكيلة ربيع 2027",
    "Defaults to the catalogue name": "يُستخدم اسم الكتالوج افتراضيًا",
    "Footer note, e.g. terms of sale": "ملاحظة التذييل، مثل شروط البيع",
    "Choose an email template.": "اختر قالب بريد إلكتروني.",
    "These recipients have no email address: %s": "هؤلاء المستلمون ليس لديهم بريد إلكتروني: %s",
    "The accent colour must look like #1D4E89.": "يجب أن يكون اللون المميز بصيغة ‎#1D4E89.",
    "The validity end must follow its start.": "يجب أن تكون نهاية الصلاحية بعد بدايتها.",
    "The advanced filter is invalid: %s": "عامل التصفية المتقدم غير صالح: %s",
    "The product limit must be positive.": "يجب أن يكون حد المنتجات موجبًا.",
    "The PDF reuse delay cannot be negative.": "لا يمكن أن تكون مدة إعادة الاستخدام سالبة.",
    "%(style)s supports %(lo)s to %(hi)s boxes per row.":
        "يدعم %(style)s من %(lo)s إلى %(hi)s مربعات في الصف.",
    "This catalogue selects %(count)s products, above its limit of %(limit)s. "
    "Narrow the filters or split it by category.":
        "يحدد هذا الكتالوج %(count)s منتجًا، وهو أكثر من الحد %(limit)s. "
        "ضيّق عوامل التصفية أو قسّمه حسب الفئة.",
    "Catalogues with different paper sizes or orientations cannot be printed in one PDF. "
    "Print them separately.":
        "لا يمكن طباعة كتالوجات بأحجام ورق أو اتجاهات مختلفة في ملف PDF واحد. اطبعها بشكل منفصل.",
    "a domain must be a list": "يجب أن يكون النطاق قائمة",
}

# Demo data (demo/product_catalog_demo.xml); product names come from
# tools/demo_ar_names.json.
DEMO_AR = {
    "New": "جديد",
    "Bestseller": "الأكثر مبيعًا",
    "Eco": "صديق للبيئة",
    "Upholstery Colour": "لون التنجيد",
    "Graphite": "جرافيت",
    "Sand": "رملي",
    "Ocean": "أزرق محيطي",
    "Trade Partners -12%": "الشركاء التجاريون ‎-12%",
    "Public Price (Catalogue Demo)": "السعر العام (عرض الكتالوج)",
    "Office Collection 2027": "تشكيلة المكاتب 2027",
    "Seating, desks, storage and lighting for modern workplaces":
        "مقاعد ومكاتب ووحدات تخزين وإضاءة لبيئات العمل الحديثة",
    "Prices subject to stock availability. Delivery within 10 working days.":
        "الأسعار حسب توفر المخزون. التوصيل خلال 10 أيام عمل.",
    "Trade Catalogue": "كتالوج الشركاء",
    "Partner prices on our full range": "أسعار الشركاء على كامل تشكيلتنا",
    "New Arrivals": "وصل حديثًا",
    "This season's additions to the range": "إضافات هذا الموسم إلى التشكيلة",
    "Breathable mesh back, adjustable lumbar support, 4D armrests and synchronised tilt.":
        "ظهر شبكي جيد التهوية، دعم قطني قابل للتعديل، مساند ذراع رباعية الاتجاه وإمالة متزامنة.",
    "Full-grain leather, pocket-spring seat, polished aluminium five-star base.":
        "جلد طبيعي كامل، مقعد بنوابض جيبية، قاعدة خماسية من الألمنيوم المصقول.",
    "Stacks up to 8 high. Polypropylene shell.": "يُكدَّس حتى 8 كراسٍ. هيكل من البولي بروبيلين.",
    "Solid oak frame and wool upholstery for reception areas.":
        "إطار من خشب البلوط الصلب وتنجيد صوفي لمناطق الاستقبال.",
    "Chrome cantilever frame, padded seat.": "إطار كابولي مطلي بالكروم ومقعد مبطن.",
    "Dual motors, 4 memory presets, anti-collision. Height 62–127 cm.":
        "محركان، 4 إعدادات محفوظة، حماية من الاصطدام. الارتفاع 62–127 سم.",
    "Shared spine for cable management, privacy screens included.":
        "عمود مشترك لتنظيم الكابلات، مع حواجز خصوصية.",
    "Fits 100 cm alcoves.": "يناسب التجاويف بعرض 100 سم.",
    "Walnut veneer, leather writing inlay, integrated power module.":
        "قشرة جوز، سطح كتابة جلدي، وحدة طاقة مدمجة.",
    "Central locking, anti-tilt, fits under all our desks.":
        "قفل مركزي، مانع للانقلاب، يناسب جميع مكاتبنا.",
    "Takes A4 and foolscap hanging files.": "يتسع لملفات معلقة بمقاس A4 وفولسكاب.",
    "Keyless combination locks, ventilated doors.": "أقفال رقمية دون مفاتيح وأبواب مهواة.",
    "Dimmable, 3 colour temperatures, USB-C charging port.":
        "قابل للتعتيم، 3 درجات حرارة لونية، منفذ شحن USB-C.",
    "UGR<19 for glare-free screen work.": "مؤشر وهج أقل من 19 للعمل على الشاشات دون إبهار.",
    "No screen glare, clamps to any monitor.": "دون انعكاس على الشاشة، يُثبَّت على أي شاشة.",
    'For 17–32" screens up to 9 kg each.': "للشاشات من 17 إلى 32 بوصة حتى 9 كجم لكل منها.",
    "Textured surface, tilt 0–30°.": "سطح محبب، إمالة 0–30°.",
    "Recycled PET felt.": "لباد من PET معاد تدويره.",
    "Class B sound absorber, pinnable.": "ماص للصوت من الفئة B، يقبل التثبيت بالدبابيس.",
}

EMAIL_BODY_AR = """<div style="margin: 0; padding: 0; font-size: 14px;" dir="rtl">
    <p>مرحبًا،</p>
    <p>
        تجدون مرفقًا أحدث كتالوج لدينا،
        <strong t-out="object.cover_title or object.name or ''">Spring Collection</strong>.
    </p>
    <p t-if="object.valid_until">
        الأسعار صالحة حتى <t t-out="format_date(object.valid_until) or ''">31/12/2026</t>.
    </p>
    <p>يسعد فريقنا مساعدتكم في عروض الأسعار والتوفر والعينات.</p>
    <p>
        مع أطيب التحيات،<br/>
        <t t-out="user.name or ''">Sales Team</t><br/>
        <t t-out="object.company_id.name or ''">My Company</t>
    </p>
</div>"""


def demo_names():
    """English -> Arabic names of the demo products (tools/demo_ar_names.json)."""
    import json
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "demo_ar_names.json")
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as handle:
        return dict(json.load(handle).values())


def main():
    pot = polib.pofile(os.path.join(HERE, "product_catalogue_pdf_advanced.pot"))
    AR.update(DEMO_AR)
    AR.update(demo_names())
    po = polib.POFile()
    po.metadata = dict(pot.metadata)
    po.metadata.update({"Language": "ar", "Content-Type": "text/plain; charset=UTF-8",
                        "Plural-Forms": "nplurals=6; plural=n==0 ? 0 : n==1 ? 1 : n==2 ? 2 : "
                                        "n%100>=3 && n%100<=10 ? 3 : n%100>=11 ? 4 : 5;"})
    missing = []
    for entry in pot:
        if entry.msgid.lstrip().startswith('<div style="margin: 0; padding: 0; font-size: 14px;">'):
            msgstr = EMAIL_BODY_AR
        else:
            msgstr = AR.get(entry.msgid, "")
        report_views = ("model_terms:ir.ui.view,arch_db:product_catalogue_pdf_advanced.catalog",
                        "model_terms:ir.ui.view,arch_db:product_catalogue_pdf_advanced.report")
        if not msgstr and any(o[0].startswith(report_views) for o in entry.occurrences):
            missing.append(entry.msgid)
        po.append(polib.POEntry(msgid=entry.msgid, msgstr=msgstr, occurrences=entry.occurrences,
                                comment=entry.comment, tcomment=entry.tcomment, flags=entry.flags))
    po.save(os.path.join(HERE, "ar.po"))
    if missing:
        print("Untranslated report terms:", missing)
        sys.exit(1)
    print("ar.po written, %d entries, %d translated" % (
        len(po), len([e for e in po if e.msgstr])))


if __name__ == "__main__":
    main()
