from odoo import fields, models


class ProfitLossDashboardPdfReport(models.AbstractModel):
    _name = "report.hc_profit_loss_dashboard.pl_dashboard_pdf"
    _description = "Profit & Loss Dashboard PDF"

    def _get_report_values(self, docids, data=None):
        docs = self.env["hc.profit.loss.dashboard.wizard"].browse(docids)
        options = (data or {}).get("options") or {}
        payload = docs.with_context(
            allowed_company_ids=options.get("company_ids")
        )._build_dashboard_payload(
            docs._normalize_options(options)
        )
        return {
            "doc_ids": docs.ids,
            "doc_model": "hc.profit.loss.dashboard.wizard",
            "docs": docs,
            "data": payload,
            "generated_on": fields.Datetime.now(),
        }


class ProfitLossDashboardXlsxReport(models.AbstractModel):
    _name = "report.hc_profit_loss_dashboard.pl_dashboard_xlsx"
    _inherit = "report.report_xlsx.abstract"
    _description = "Profit & Loss Dashboard XLSX"

    def generate_xlsx_report(self, workbook, data, records):
        options = (data or {}).get("options") or {}
        payload = records.with_context(
            allowed_company_ids=options.get("company_ids")
        )._build_dashboard_payload(
            records._normalize_options(options)
        )
        sheet = workbook.add_worksheet("Profit and Loss")
        title_fmt = workbook.add_format(
            {
                "bold": True,
                "font_size": 15,
                "align": "center",
                "valign": "vcenter",
            }
        )
        subtitle_fmt = workbook.add_format(
            {"font_size": 10, "align": "center", "font_color": "#52616B"}
        )
        header_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#DDE7F2",
                "border": 1,
                "align": "left",
            }
        )
        cell_fmt = workbook.add_format({"border": 1, "align": "left"})
        amount_fmt = workbook.add_format(
            {"border": 1, "align": "right", "num_format": "#,##0.00"}
        )
        amount_bold_fmt = workbook.add_format(
            {
                "border": 1,
                "align": "right",
                "num_format": "#,##0.00",
                "bold": True,
                "bg_color": "#F4F7FA",
            }
        )
        section_fmt = workbook.add_format(
            {
                "bold": True,
                "bg_color": "#F7FAFC",
                "border": 1,
                "align": "left",
            }
        )
        sheet.set_column("A:A", 42)
        sheet.set_column("B:B", 18)
        sheet.merge_range("A1:B1", "Profit & Loss Dashboard", title_fmt)
        sheet.merge_range("A2:B2", payload["meta"]["period_label"], subtitle_fmt)
        row = 4
        for metric_key in (
            "revenue",
            "expenses",
            "net_profit",
            "gross_margin_pct",
            "operating_profit",
        ):
            metric = payload["kpis"][metric_key]
            sheet.write(row, 0, metric["label"], header_fmt)
            if metric["type"] == "percentage":
                sheet.write(row, 1, metric["value"] / 100.0, workbook.add_format(
                    {"border": 1, "align": "right", "num_format": "0.00%"}
                ))
            else:
                sheet.write(row, 1, metric["value"], amount_fmt)
            row += 1
        row += 1
        sheet.write(row, 0, "Statement", header_fmt)
        sheet.write(row, 1, payload["meta"]["currency_name"], header_fmt)
        row += 1
        sections = {section["key"]: section for section in payload["statement"]["sections"]}
        summaries = {row_data["key"]: row_data for row_data in payload["statement"]["summary_rows"]}
        for layout_row in payload["statement"]["layout"]:
            if layout_row["type"] == "section":
                section = sections[layout_row["key"]]
                sheet.write(row, 0, section["label"], section_fmt)
                sheet.write(row, 1, section["amount"], amount_bold_fmt)
                row += 1
                for account in section["accounts"]:
                    account_label = account["code"] and "%s - %s" % (account["code"], account["name"]) or account["name"]
                    sheet.write(row, 0, account_label, cell_fmt)
                    sheet.write(row, 1, account["amount"], amount_fmt)
                    row += 1
            else:
                summary_row = summaries[layout_row["key"]]
                sheet.write(row, 0, summary_row["label"], section_fmt)
                sheet.write(row, 1, summary_row["amount"], amount_bold_fmt)
                row += 1
