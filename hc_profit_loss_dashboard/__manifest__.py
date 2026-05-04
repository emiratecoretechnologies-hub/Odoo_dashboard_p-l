{
    "name": "Profit & Loss Dashboard",
    "summary": "Interactive Profit & Loss dashboard for Accounting",
    "author": "Emiratecore Technologies",
    "website": "linkedin.com/in/emiratecore-technologies-4a0519407",
    "category": "Accounting/Accounting",
    "version": "18.0.1.0.0",
    "license": "AGPL-3",
    "depends": ["account", "web", "report_xlsx"],
    "data": [
        "security/ir.model.access.csv",
        "report/profit_loss_dashboard_templates.xml",
        "report/profit_loss_dashboard_actions.xml",
        "views/profit_loss_dashboard_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "hc_profit_loss_dashboard/static/src/js/profit_loss_dashboard.js",
            "hc_profit_loss_dashboard/static/src/scss/profit_loss_dashboard.scss",
            "hc_profit_loss_dashboard/static/src/xml/profit_loss_dashboard.xml",
        ],
    },
    "installable": True,
    "application": True,
}
