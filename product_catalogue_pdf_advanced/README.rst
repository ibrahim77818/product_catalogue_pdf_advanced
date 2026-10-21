==============================
Product Catalogue PDF Advanced
==============================

Print-accurate, pricelist-aware, bilingual (LTR/RTL) PDF product catalogues
for Odoo 16, with deterministic pagination, availability badges, QR links and
email delivery that never leaks one customer's prices to another.

.. contents::
   :local:

Why it is built this way
========================

Odoo 16 prints with **wkhtmltopdf 0.12.x (QtWebKit, ~2012)**. That engine has
no CSS Grid, unreliable ``flex-wrap``, no CSS variables, no ``object-fit``,
ignores ``@page`` margin boxes and treats ``page-break-*`` as a hint. A
catalogue that relies on "modern CSS paged media" looks right in the HTML
preview and breaks on paper: cards sliced between sheets, drifting headers.

So the layout is decided in Python, before any HTML exists:

``models/catalog_layout.py`` (ORM-free)
    A metric model per style returns the exact height, in millimetres, of a
    row of cards for the chosen columns, image size and visibility toggles.
    Text is truncated to the lines its box can hold. ``paginate`` packs
    category headings and rows into pages of known height. It keeps a heading
    with its first row and repeats it as "continued" on the next page.
    ``build_plan`` adds the cover and table of contents, then numbers the pages.

``report/catalog_report_templates.xml``
    Draws the plan. Every page is a fixed-size box with
    ``page-break-after: always``; rows are fixed-width tables. wkhtmltopdf
    never has to choose where a page breaks.

``report.paperformat`` × 4
    Zero margins, 96 dpi and no smart shrinking, so one CSS millimetre is one
    printed millimetre. Each catalogue picks A4 or Letter, in portrait or
    landscape.

Features
========

* **Three styles**:

  * Grid cards, 1–4 per row.
  * Compact list, 1–2 per row.
  * Showcase, 1–2 per row, with large imagery.

* **Theme**: accent colour, cover page (title, subtitle, image, key facts,
  categories), table of contents.
* **Accessible accent colour**: the accent is darkened automatically when
  needed so printed prices keep a WCAG contrast of at least 4.5:1, and text
  on the cover switches to dark ink on light brand colours.
* **Visibility toggles**: image, internal reference, description (sales or
  product description), price, price before discount, QR link, availability.
* **Selection**:

  * Categories (sub-categories included), tags, attribute values.
  * Explicit include and exclude lists.
  * Saleable products only.
  * An advanced domain.
  * Sort by name, reference or price.

* **Prices**:

  * Computed by Odoo's own pricelist engine (``_compute_price_rule``) for
    each variant, then folded into "From X" when variants differ.
  * Public price struck through for pricelists that show the discount.
  * Tax included or excluded (customer taxes, when Invoicing is installed).

* **Availability**:

  * Free quantity in one warehouse, printed as *In stock*, *Low stock* or
    *On request*. Exact quantities are never printed.
  * Products that are out of stock can be hidden.

* **Snapshot stamp**: every page footer prints the currency, tax mode,
  pricelist, warehouse, generation time and validity.
* **QR codes**:

  * Generated as vector SVG with the ``qrcode`` library: sharp at any size,
    a few hundred bytes each.
  * The link comes from a URL pattern, or from the eCommerce page when
    Website Sale is installed.
  * A placeholder that resolves to nothing produces no QR code rather than
    a broken one.

* **Right-to-left**:

  * Arabic and other RTL languages mirror the layout.
  * IBM Plex Sans Arabic (SIL OFL) is embedded and covers both Latin and
    Arabic.
  * Arabic translation of every printed label (``i18n/ar.po``).

* **Sending**:

  * Recipients are grouped by *(pricelist, language)*.
  * Each group gets one PDF rendered with its own prices and in its own
    language.
  * The wizard shows the grouping before sending.
  * Each send is logged in the chatter.

* **Caching**:

  * A rendered PDF is reused while the configuration, products and
    pricelist rules are unchanged, within a configurable time window.
  * Saving the catalogue's own "last generated" date does not invalidate
    it.

Demo data
=========

Installed when the database has demo data enabled
(``demo/product_catalog_demo.xml``):

* **Products**: 25 office-furniture products in 5 categories under
  *Office Furniture*.

  * Generated illustrations ship in ``static/img/demo``; three products have
    no image on purpose, to show the placeholder.
  * Tags: *New*, *Bestseller*, *Eco*.
  * The *ErgoFlex* chair has three colour variants, one with a +120
    surcharge, so it prints "From …".

* **Stock**: quantities in the main warehouse cover every availability
  badge (plenty, below 5, none).
* **Pricelists**: *Trade Partners -12%* shows its discount, so the public
  price prints struck through.
* **Customers**: two on the trade pricelist and one on the public pricelist.
  Sending a catalogue to all three produces two PDFs, one per pricelist.
* **Ready-to-print catalogues**:

  * *Office Collection 2027*: grid of 3, cover image, table of contents, QR
    codes and availability.
  * *Trade Catalogue*: grid of 4 on the trade pricelist.
  * *Price List*: compact list, no cover.
  * *New Arrivals Lookbook*: landscape showcase of the *New* tag.

* **Arabic**: ``i18n/ar.po`` translates the demo product names,
  descriptions, tags and cover texts. With Arabic installed, send the
  *Trade Catalogue* in Arabic or print it with the language set to Arabic.

Performance
===========

Two things keep memory flat and rendering fast:

* **Reading**:

  * Product ids come from one search.
  * Products are read in slices of 200, fetching only the printed fields.
  * Each slice's record cache is released before the next slice is read.

* **Images**: the embedded image field is chosen from the printed size and
  print quality (110, 150 or 300 ppi). A 42 mm box at 150 ppi needs
  ``image_256``, not ``image_1920``.

The real memory hot spot is wkhtmltopdf, not the ORM: its footprint grows with
the HTML it lays out. Because the page plan is final before rendering, the
document is rendered in **independent batches of 40 pages**, which are then
merged.

Measured on 1,200 products, grid 4 per row, QR and availability on:

==========================  =================  ===================
                            single render      40-page batches
==========================  =================  ===================
pages (plan = PDF)          152 = 152          152 = 152
wkhtmltopdf peak memory     1,028 MB           313 MB
Odoo worker peak memory     324 MB             233 MB
time to PDF                 23 s               30 s
==========================  =================  ===================

A test asserts that the number of SQL queries does not grow with the number
of products (no N+1).

Requirements
============

* Odoo 16.0 (Community or Enterprise). Depends on ``product``, ``stock`` and
  ``mail``. ``account`` is optional (for tax-included prices).
* Python ``qrcode``, which is already in Odoo's ``requirements.txt``.
* **wkhtmltopdf 0.12.6.1 built with patched Qt**, as Odoo requires.

  * With an unpatched build, ``--disable-smart-shrinking`` is ignored and
    every page renders at about 80 % scale.
  * Check with ``wkhtmltopdf -V``; it must say "with patched qt".

* ``report.url`` (or ``web.base.url``) must be reachable from the server
  itself. wkhtmltopdf downloads the stylesheet and fonts from there.

Security
========

* **Product Catalogues / User**: read, print and send catalogues. Users can
  log the emails they send in the chatter without having write access.
* **Product Catalogues / Administrator**: create and configure catalogues.
* **Multi-company**: a record rule on ``company_id``. Pricelist and warehouse
  are checked against the catalogue's company.
* **Stock**: availability is computed as superuser, so people without
  Inventory rights still see bands. Only the band leaves that computation.

Verification
============

Each check uses an oracle outside the module's own logic:

``tests/test_catalogue.py`` (``--test-tags /product_catalogue_pdf_advanced``)
    * **Layout properties**:

      * Every combination of style, columns, image size and toggles on
        A4/Letter, portrait/landscape fits a row on a page.
      * Randomised pagination never overflows a page, loses or duplicates a
        card, or leaves a heading orphaned at the bottom.
      * Table-of-contents page numbers point at the right page.

    * **Prices** are compared with ``pricelist._get_product_price`` and taxes
      with ``account.tax.compute_all``.
    * **Rules and access**: visibility toggles checked in the rendered
      report; stock bands; filters; constraints; access rights;
      multi-company rule.
    * **Sending**: recipients grouped by pricelist; one email and one
      attachment per group; a cached PDF is reused.
    * **No N+1**: query count is checked as the product count grows.
    * **Real PDFs** (``HttpCase``, wkhtmltopdf): the page count read from the
      PDF bytes equals the plan; the merged batches keep global numbering;
      hidden prices are absent from the PDF text.

``tools/inspect_catalog_pdf.py``
    Checks a PDF from the outside, with poppler and pixels:

    * The top bar spans the full sheet, edge to edge.
    * No content is left in the bottom safety margin.
    * Every product reference appears on exactly one page.
    * QR codes decode to the expected URLs.

Known limits
============

* Product category names are not translatable in Odoo 16, so they print as
  stored.
* "Tax included" applies the product's customer taxes without fiscal
  position mapping. Use "Tax excluded" for cross-border catalogues.
* Availability is a snapshot taken when the PDF is generated. A cached PDF is
  at most *Reuse Generated PDF For* minutes old, and the footer shows when it
  was generated.

Credits
=======

Author: Ibrahim Yahia — https://www.linkedin.com/in/ibrahim-yahia

Fonts: IBM Plex Sans Arabic © IBM Corp., SIL Open Font License 1.1
(``static/fonts/OFL-IBM-Plex.txt``).
