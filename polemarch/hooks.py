app_name = "polemarch"
app_title = "Polemarch Customizations"
app_publisher = "Mithtech Innovative Solutions PVT LTD"
app_description = "All customizations needed for polemarch"
app_email = "manoj311093@gmail.com"
app_license = "mit"

after_install = "polemarch.install.after_install"
after_migrate = "polemarch.install.after_migrate"

app_logo_url = "/assets/polemarch/images/polemarch-favicon.png"
website_context = {
    "favicon": "/assets/polemarch/images/polemarch-favicon.png",
    "splash_image": "/assets/polemarch/images/polemarch-favicon.png",
}

doctype_js = {
    "Customer": "public/js/customer.js",
    "Sales Invoice": "public/js/sales_invoice.js",
    "Item": "public/js/item.js",
}

doctype_list_js = {
    "Customer": "public/js/customer_list.js",
    "Item": "public/js/item_list.js",
    "Sales Invoice": "public/js/sales_invoice_list.js",
}

doc_events = {
    "Customer": {
        "validate": "polemarch.overrides.customer.validate",
        "on_update": "polemarch.overrides.customer.on_update",
    },
    "Item": {
        "on_update": "polemarch.overrides.item.on_update",
        "on_trash": "polemarch.overrides.item.on_trash",
    },
    "Sales Invoice": {
        "validate": "polemarch.overrides.sales_invoice.validate",
        "on_submit": "polemarch.overrides.sales_invoice.on_submit",
        "on_cancel": "polemarch.overrides.sales_invoice.on_cancel",
    },
    "Sales Order": {
        "validate": "polemarch.overrides.sales_order.validate",
    },
}

scheduler_events = {
    "hourly": [
        "polemarch.medusa.reconcile.run_hourly",
    ],
}

fixtures = [
    {"doctype": "Brand", "filters": [["name", "in", ["Polemarch", "Mithtech Services"]]]},
    {"doctype": "Customer Group", "filters": [["name", "=", "Polemarch"]]},
]
