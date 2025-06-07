from django import forms
from django.contrib.postgres.forms import SimpleArrayField
from django.forms import Textarea

from contify.website_tracking.models import WebSource


class WebSourceAdminForm(forms.ModelForm):
    def __init__(self, *args, **kwargs):
        super(WebSourceAdminForm, self).__init__(*args, **kwargs)
        self.fields['junk_xpaths'] = SimpleArrayField(
            forms.CharField(), delimiter='|', required=False,
            widget=Textarea(attrs={'rows': '5', 'cols': '100'})
        )
        self.fields['junk_xpaths'].help_text = (
            "<p style='color:#515254; font-size: 14px'>"
            "  Multiple values should be separated by character: "
            "  <b style='color: #ff0000'>|</b>"
            "</p>"
            "<p style='color: green; font-size: 14px'>"
            "   Eg: //ul[@id='djDebugPanelList']//li[3]<b style='color: "
            "#ff0000'>|</b>//iframe[contains(@src,'google.com/recaptcha')][1]"
            "</p>"
        )
        self.fields['screenshot_sleep_time'].help_text = (
            "<p>Used to store sleep time in seconds which is use to wait and load "
            "webpages completely before taking screeshot</p>"
        )
        self.fields['accept_cookie_xpaths'] = SimpleArrayField(
            forms.CharField(),
            delimiter='$',
            required=False,
            widget=Textarea(attrs={'rows': '5', 'cols': '100'})
        )
        self.fields['accept_cookie_xpaths'].help_text = (
                "<p style='color:#515254; font-size: 14px'>"
                "Multiple XPath values should be separated by the character: "
                "<b style='color: #ff0000'>$</b>"
                "</p>"
                "<p style='color: green; font-size: 14px'>"
                "Eg: //div[@id='cookie-banner']//button[text()='Accept']"
                "<b style='color: #ff0000'>$</b>"
                "//div[contains(@class, 'cookie-consent')]"
                "//button[@id='accept']"
                "</p>"
            )

    class Meta:
        model = WebSource
        exclude = ()
