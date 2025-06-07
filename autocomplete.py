
# -*- coding: utf-8 -*-

# Python Imports
import json

# Django Imports
from django import forms
from django.contrib import admin
from django.contrib.admin.options import InlineModelAdmin
from django.db import models
from django.http import HttpResponse, HttpResponseNotFound
from django.urls import re_path
from django.utils.datastructures import MultiValueDict
from django.utils.safestring import mark_safe

from django.conf import settings
from django.apps import apps

from config.constants import ADMIN_CLIENT_ID


get_model = apps.get_model

edit_icons8_base_64 = (
    """
    <img src="data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAA8AAAAPCAYAAAA71p
    VKAAAAAXNSR0IArs4c6QAAAARnQU1BAACxjwv8YQUAAAAJcEhZcwAADsMAAA7DAcdvqGQAAAEy
    SURBVDhPvY8/T8JQFMU7GePg7lIXYJAugot0MPifBrDRThWTkigutUqULprUBcNQhUmYaFyNwm
    JcpMHJT+C38Duc66u+sbF10F9yc+6975z38oR/YaZGU7z9HUkLp/IV/ISFCl/FY76KpnqBUdcn
    srp4W6jFvGC5AjOvo7eyi8Zli15fnogObfjFqC+oGkx1BwVWrqpRa1tD4/wII13HGbeEY5RgGl
    tQjDLcvSKJgVZL6FXLaHJLOPV1mPVNKCcbcK1VEgM9XkMh2HNLOM4STCcPhanrsOCXfs8/B9s5
    HLQX0WXqdmQSA73OQenIEcGAfgZ3Xha2l8EN691+Fgqr6KA3S5ODNN0PJVKHadiPEm4HUoxgwH
    MC034SH34KD6y8cQr7/CgeY/b6+xxN8PGvEYRPHH+bMiFG7B4AAAAASUVORK5CYII=">
    """
)


class AutocompleteModelAdmin(admin.ModelAdmin):
    array_as_m2m_search_fields = dict()
    integer_as_fk_search_fields = dict()
    related_search_fields = dict()

    def get_urls(self):
        default_urls = super(AutocompleteModelAdmin, self).get_urls()
        custom_urls = [
            re_path(
                r'^search/$', self.admin_site.admin_view(self.search),
                name='search'
            )
        ]
        return custom_urls + default_urls

    @staticmethod
    def search(request):
        """
        Searches in the fields of the given related model and returns the
        result as a simple string to be used by the jQuery Autocomplete plugin
        """
        query = request.GET.get('q')
        app_label = request.GET.get('app_label')
        model_name = request.GET.get('model_name')

        model_field_name = request.GET.get('field_name')
        search_fields = request.GET.get('search_fields')
        client_id = request.GET.get('clientID')
        client_company_id = request.GET.get('clientCompanyID')
        extra_qs_filter = request.GET.get("extraQSFilter")
        num_items = request.GET.get("numItems") or 15

        if search_fields and app_label and model_name and query:
            def construct_query(query, field_name='name'):
                # use different lookup methods depending on the notation
                if field_name == "company_preferences":
                    return "company_preferences__active", True
                elif query.startswith('^'):
                    return "%s__istartswith" % field_name, query[1:]
                elif query.startswith('='):
                    return "%s__iexact" % field_name, query[1:]
                elif query.startswith('@'):
                    return "%s__search" % field_name, query[1:]
                else:
                    if model_name in ["person", "company", "subscriber"]:
                        return "%s__istartswith" % field_name, query
                    else:
                        return "%s__icontains" % field_name, query

            model = get_model(app_label, model_name)
            q = None

            for field_name in search_fields.split(','):
                name, query = construct_query(query, field_name)
                if q:
                    if not str(name) == "company_preferences__active":
                        q = q | models.Q(**{str(name): query})
                    else:
                        q = q & models.Q(**{str(name): query})
                else:
                    q = models.Q(**{str(name): query})

            qs = model.objects.filter(q)

            if extra_qs_filter:
                qs = qs.filter(**json.loads(extra_qs_filter))

            if hasattr(model(), 'is_active'):
                qs = qs.filter(is_active=True).filter(q)
            elif hasattr(model(), 'active'):
                qs = qs.filter(active=True)

            if model_name in ['customtag']:
                if client_id:
                    qs = qs.filter(
                        bucket__company__company_preferences__id=client_id
                    )
                elif client_company_id:
                    qs = qs.filter(bucket__company__id=client_company_id)
                qs = qs.filter(name_translations__icontains=query)

            rel_name = field_name.split('__')[0]

            # ToDo: use extraQSFilter
            if model_name == "rssfeed":
                qs = qs.filter(show_for_client=ADMIN_CLIENT_ID)
            qs = qs[:int(num_items)]

            if model_name == 'customtag':
                cxt = [
                    {
                        'label': '({}) - {}\n'.format(f[1], f[2]),
                        'id': f[0]
                    }
                    for f in list(qs.values_list("id", "bucket__name", "name"))
                ]
            elif model_name == "customtrigger":
                cxt = [
                    {
                        'label': '{} - ({})\n'.format(f[1], f[2]),
                        'id': f[0]
                    }
                    for f in list(
                        qs.values_list("id", "name", "created_by__username")
                    )
                ]
            else:
                cxt = [
                    {
                        'label': '{} \n'.format(getattr(f, rel_name)),
                        'id': f.pk
                    } for f in qs
                ]
            return HttpResponse(json.dumps(cxt))
        return HttpResponseNotFound()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.array_as_m2m_search_fields:
            search_info = self.array_as_m2m_search_fields[db_field.name]
            kwargs['widget'] = ManyToManySearchInputRelated(
                db_field, search_info["related_model"],
                search_info["search_fields"],
                sub_labels=search_info.get("sub_labels"),
                to_field_name=search_info.get("to_field_name"),
                arr_val_sep=search_info.get("arr_val_sep", ","),
                show_item_admin_link=search_info.get("show_item_admin_link"),
                is_readonly=search_info.get("is_readonly"),
                extra_qs_filter=search_info.get("extra_qs_filter"),
                num_items=search_info.get("num_items")
            )
            db_field.help_text = ''
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        elif db_field.name in self.integer_as_fk_search_fields:
            search_info = self.integer_as_fk_search_fields[db_field.name]
            kwargs['widget'] = SingleSearchInputRelated(
                db_field, search_info["related_model"],
                search_info["search_fields"], search_info.get("to_field_name"),
                show_item_admin_link=search_info.get("show_item_admin_link"),
                is_readonly=search_info.get("is_readonly"),
                extra_qs_filter=search_info.get("extra_qs_filter"),
                num_items=search_info.get("num_items")
            )
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        elif db_field.name in self.related_search_fields:
            search_info = self.related_search_fields[db_field.name]
            if isinstance(db_field, models.ForeignKey):
                kwargs['widget'] = SingleSearchInputRelated(
                    db_field, db_field.remote_field.model,
                    search_info["search_fields"],
                    show_item_admin_link=search_info.get("show_item_admin_link"),
                    is_readonly=search_info.get("is_readonly"),
                    extra_qs_filter=search_info.get("extra_qs_filter"),
                    num_items=search_info.get("num_items")
                )

            if isinstance(db_field, models.ManyToManyField):
                kwargs['widget'] = ManyToManySearchInputRelated(
                    db_field, db_field.remote_field.model,
                    search_info["search_fields"],
                    sub_labels=search_info.get("sub_labels"),
                    show_item_admin_link=search_info.get("show_item_admin_link"),
                    is_readonly=search_info.get("is_readonly"),
                    extra_qs_filter=search_info.get("extra_qs_filter"),
                    num_items=search_info.get("num_items")
                )
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        return super(AutocompleteModelAdmin, self).formfield_for_dbfield(
            db_field, request, **kwargs
        )


class AutocompleteInlineModelAdmin(InlineModelAdmin):
    array_as_m2m_search_fields = dict()
    integer_as_fk_search_fields = dict()
    related_search_fields = dict()

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.array_as_m2m_search_fields:
            search_info = self.array_as_m2m_search_fields[db_field.name]
            kwargs['widget'] = ManyToManySearchInputRelated(
                db_field, search_info["related_model"],
                search_info["search_fields"],
                sub_labels=search_info.get("sub_labels"),
                to_field_name=search_info.get("to_field_name"),
                arr_val_sep=search_info.get("arr_val_sep", ","),
                show_item_admin_link=search_info.get("show_item_admin_link"),
                is_readonly=search_info.get("is_readonly"),
                extra_qs_filter=search_info.get("extra_qs_filter"),
                num_items=search_info.get("num_items")
            )
            db_field.help_text = ''
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        elif db_field.name in self.integer_as_fk_search_fields:
            search_info = self.integer_as_fk_search_fields[db_field.name]
            kwargs['widget'] = SingleSearchInputRelated(
                db_field, search_info["related_model"],
                search_info["search_fields"], search_info.get("to_field_name"),
                show_item_admin_link=search_info.get("show_item_admin_link"),
                is_readonly=search_info.get("is_readonly"),
                extra_qs_filter=search_info.get("extra_qs_filter"),
                num_items=search_info.get("num_items")
            )
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        elif db_field.name in self.related_search_fields:
            search_info = self.related_search_fields[db_field.name]
            if isinstance(db_field, models.ForeignKey):
                kwargs['widget'] = SingleSearchInputRelated(
                    db_field, db_field.remote_field.model,
                    search_info["search_fields"],
                    show_item_admin_link=search_info.get("show_item_admin_link"),
                    is_readonly=search_info.get("is_readonly"),
                    extra_qs_filter=search_info.get("extra_qs_filter"),
                    num_items=search_info.get("num_items")
                )

            if isinstance(db_field, models.ManyToManyField):
                kwargs['widget'] = ManyToManySearchInputRelated(
                    db_field, db_field.remote_field.model,
                    search_info["search_fields"],
                    sub_labels=search_info.get("sub_labels"),
                    show_item_admin_link=search_info.get("show_item_admin_link"),
                    is_readonly=search_info.get("is_readonly"),
                    extra_qs_filter=search_info.get("extra_qs_filter"),
                    num_items=search_info.get("num_items")
                )
            form_field = db_field.formfield(**kwargs)
            form_field.hidden_widget.is_hidden = False
            return form_field
        return super(AutocompleteInlineModelAdmin, self).formfield_for_dbfield(
            db_field, request, **kwargs
        )


class AutocompleteStackedInline(AutocompleteInlineModelAdmin):
    template = 'admin/edit_inline/stacked.html'


class AutocompleteTabularInline(AutocompleteInlineModelAdmin):
    template = 'admin/edit_inline/tabular.html'


class SingleSearchInputRelated(forms.HiddenInput):
    def get_display_name(self, value):
        if not value:
            return ""
        rel_name = self.search_fields[0].split('__')[0]
        to_field = self.to_field_name or (
            self.related_model._meta.pk and self.related_model._meta.pk.name
        )
        obj = self.related_model._default_manager.get(**{to_field: value})
        return getattr(obj, rel_name, '')

    def __init__(self, db_field, related_model, search_fields,
                 to_field_name=None, show_item_admin_link=False,
                 is_readonly=False, extra_qs_filter=None, num_items=None,
                 attrs=None):
        self.db_field = db_field
        self.search_fields = search_fields
        self.related_model = related_model
        self.to_field_name = to_field_name
        self.show_item_admin_link = show_item_admin_link
        self.is_readonly = is_readonly
        self.extra_qs_filter = extra_qs_filter or {}
        self.num_items = num_items or 15
        self.app_label = related_model._meta.app_label
        self.model_name = related_model._meta.model_name
        super(SingleSearchInputRelated, self).__init__(attrs)

    def get_autocomplete_html(self, name, value):
        label = self.get_display_name(value)

        if self.is_readonly:
            input_size = len(label) - 1 if value else 0
            html = """
                <input type="text" id="lookup_{name}" value="{label}" size="{size}" readonly />
            """.format(name=name, label=label, size=input_size)
            return html

        raw_html = """
            <input type="text" id="lookup_%(name)s" value="%(label)s" size="40" />

            <script type="text/javascript">
                var cache = {};

                function getSourceUrl() {
                    var currentPath = window.location.pathname
                    splittedPath = currentPath.split('/');
                    splittedPath.pop();
                    splittedPath.pop();
                    if (currentPath.endsWith('change/')) {
                        splittedPath.pop();
                    }
                    return splittedPath.join('/') + '/search/'
                }

                django.jQuery(document).ready(function() {
                    getSourceUrl();

                    function liFormat_%(name)s (row, i, num) {
                        var result = row[0] ;
                        return result;
                    }

                    function selectItem_%(name)s(event, ui) {
                        if(ui == null) {
                            return
                        }
                        django.jQuery("#id_%(name1)s").val(ui.item.id);
                    }

                    //AutoComplete
                    django.jQuery(django.jQuery("#lookup_%(name)s")).autocomplete({
                        source: function(request, response) {
                            var dataToSend = django.jQuery.extend(getExtraParams(), {'q': request.term});
                            var strTerm = JSON.stringify(dataToSend);
                            if (cache.hasOwnProperty(strTerm)) {
                                response(cache[strTerm]);
                                return;
                            }
                            django.jQuery.get(getSourceUrl(), dataToSend,
                                function(data) {
                                    var jsonData = JSON.parse('['+data+']')[0]
                                    cache[strTerm] = jsonData;
                                    response(jsonData);
                                }
                            );
                        },
                        // Disabling messages
                        messages: {
                            noResults: '',
                            results: function() {}
                        },
                        minLength: 2,
                        select: selectItem_%(name)s,
                        formatItem: liFormat_%(name)s,
                    });

                    //Fetching params
                    function getExtraParams() {
                        return {
                            search_fields: '%(search_fields)s',
                            app_label: '%(app_label)s',
                            model_name: '%(model_name)s',
                            extraQSFilter: '%(extra_qs_filter)s',
                            numItems: '%(num_items)s',
                        }
                    }

                });
            </script>
        """
        label = self.get_display_name(value)
        html = mark_safe(raw_html) % {
            'search_fields': ','.join(self.search_fields),
            'STATIC_URL': settings.STATIC_URL,
            'model_name': self.model_name,
            'app_label': self.app_label,
            'label': label,
            'name': name.replace('-', '_'),
            'name1': name,
            'value': value,
            'field_name': name,
            "extra_qs_filter": self.extra_qs_filter,
            "num_items": self.num_items
        }
        return html

    def get_admin_link_html(self, value):
        admin_link = ""
        if self.show_item_admin_link and value:
            title = "View {field}".format(
                field=self.db_field.name.replace("_", " ").title()
            )
            app_label = self.app_label
            model_name = self.model_name
            if self.db_field.name == "client":
                app_label = "subscriber"
                model_name = "companypreferences"
            admin_link = """
                <a href="/admin/{}/{}/{}"
                    target="_blank"
                    title="{}">
                    {}
                </a>
            """.format(
                app_label, model_name, value, title,
                edit_icons8_base_64
            )
        return admin_link

    def render(self, name, value, attrs=None, renderer=None):
        attrs = attrs or {}
        rendered = super(SingleSearchInputRelated, self).render(
            name, value, attrs, renderer
        )
        autocomplete_html = self.get_autocomplete_html(name, value)
        admin_link_html = self.get_admin_link_html(value)

        return rendered + autocomplete_html + admin_link_html


class ManyToManySearchInputRelated(forms.MultipleHiddenInput):
    """
    A Widget for displaying ForeignKeys in an autocomplete search input
    instead in a <select> box.
    """
    def get_related_query_set(self, value):
        to_field = self.to_field_name or (
            self.related_model._meta.pk and self.related_model._meta.pk.name
        )
        filters = {"{}__in".format(to_field): value}
        return self.related_model.objects.filter(**filters)

    def __init__(self, db_field, related_model, search_fields,
                 sub_labels=None, to_field_name=None, arr_val_sep=None,
                 show_item_admin_link=False, is_readonly=False,
                 extra_qs_filter=None, num_items=None, attrs=None):
        self.db_field = db_field
        self.search_fields = search_fields
        self.related_model = related_model
        self.to_field_name = to_field_name
        self.sub_labels = sub_labels or []
        self.array_value_separator = arr_val_sep
        self.show_item_admin_link = show_item_admin_link
        self.is_readonly = is_readonly
        self.extra_qs_filter = extra_qs_filter or {}
        self.num_items = num_items or 15
        self.app_label = related_model._meta.app_label
        self.model_name = related_model._meta.model_name
        self.help_text = ""
        super(ManyToManySearchInputRelated, self).__init__(attrs)

    def get_admin_link_html(self, value):
        admin_link = ""
        if self.show_item_admin_link and value:
            title = "View {field}".format(
                field=self.db_field.name.replace("_", " ").title()
            )
            admin_link = """
                <a href="/admin/{}/{}/{}"
                    target="_blank"
                    title="{}">
                    {}
                </a>
            """.format(
                self.app_label, self.model_name, value, title,
                edit_icons8_base_64
            )
        return admin_link

    def get_display_html(self, obj, name, extra_label):
        display_name = getattr(obj, self.search_fields[0].split('__')[0])
        div_class = ""

        if extra_label:
            display_name = "{} {}".format(display_name, extra_label)

        if not self.is_readonly:
            div_class = "'to_delete deletelink'"

        raw_html = """
            <div class='to_delete_div'>
                <span class={} style="cursor:pointer;"></span>
                <input type='hidden' name='%(name)s' value='%(value)s'/>%(label)s
                {}
            </div>
        """.format(div_class, self.get_admin_link_html(obj.id))
        html = mark_safe(raw_html) % {
            "label": display_name,
            "name": name.replace('-', '_'),
            "value": obj.id,
        }
        return html

    def value_from_datadict(self, data, files, name):
        if isinstance(data, MultiValueDict):
            res = data.getlist(name.replace('-', '_'))
        else:
            res = data.get(name.replace('-', '_'), None)
        return res

    def get_autocomplete_html(self, name, value, selected):
        raw_html = """
             <input type="text" id="lookup_%(name)s" value="" size="40"/>%(label)s

             <div style="float:left; padding-left:105px; width:300px;">
                 <font  style="color:#999999;font-size:10px !important;">
                    %(help_text)s
                 </font>

                <div id="box_%(name)s" style="padding-left:20px;">
                    %(selected)s
                </div>
            </div>

            <script type="text/javascript">
                var cache = {};

                function removeItem() {
                    django.jQuery(".to_delete").click(function (event) {
                        event.preventDefault();
                        event.stopPropagation();
                        django.jQuery(this).parent().remove();
                    });
                }

                django.jQuery(document).ready(function() {
                    // --- delete initial element ---
                    removeItem();

                    function getSourceUrl() {
                        var currentPath = window.location.pathname
                        splittedPath = currentPath.split('/');
                        splittedPath.pop();
                        splittedPath.pop();
                        if (currentPath.endsWith('change/')) {
                            splittedPath.pop();
                        }
                        return splittedPath.join('/') + '/search/'
                    }

                    function liFormat_%(name)s (row, i, num){
                        var result = row[0] ;
                        return result;
                    }

                    function selectItem_%(name)s(event, ui) {
                        event.preventDefault();
                        event.stopPropagation();
                        var input_ele;
                        var delete_ele;
                        var div_ele;
                        if(ui == null) {
                            return
                        }
                        django.jQuery(django.jQuery("#lookup_%(name)s")).val("");

                        // new element ---
                        input_ele = '<input type="hidden" name="%(name)s" value="'+ui.item.id+'"/>'
                        delete_ele = '<span class="to_delete deletelink" style="cursor:pointer;"></span>'
                        div_ele = '<div class="to_delete_div">'+ delete_ele + input_ele + ui.item.label +'</div>'
                        django.jQuery(div_ele).click(
                            function () {
                                event.preventDefault();
                                event.stopPropagation();
                                django.jQuery(this).remove();
                            }
                        ).appendTo("#box_%(name)s");

                        return false;
                    }

                    //AutoComplete
                    django.jQuery(django.jQuery("#lookup_%(name)s")).autocomplete({
                        source: function(request, response) {
                            var dataToSend = django.jQuery.extend(getExtraParams(), {'q': request.term});
                            var strTerm = JSON.stringify(dataToSend);
                            if (cache.hasOwnProperty(strTerm)) {
                                response(cache[strTerm]);
                                return;
                            }
                            django.jQuery.get(getSourceUrl(), dataToSend,
                                function(data) {
                                    var jsonData = JSON.parse('['+data+']')[0]
                                    cache[strTerm] = jsonData;
                                    response(jsonData);
                                }
                            );
                        },
                        // Disabling messages
                        messages: {
                            noResults: '',
                            results: function() {}
                        },
                        minLength: 2,
                        select: selectItem_%(name)s,
                        formatItem:liFormat_%(name)s,
                    });

                    //Fetching params
                    function getExtraParams() {
                        var clientIDEle;
                        var clientEle;
                        var clientID;
                        var showForClientEle;
                        var clientCompanyID;

                        showForClientEle = django.jQuery('input[name="show_for_client"]');
                        clientIDEle = django.jQuery('input[name="client_id"]');
                        clientEle = django.jQuery('input[name="client"]');

                        if (showForClientEle.length === 1) {
                            clientCompanyID = showForClientEle[0].value
                        }

                        if (clientIDEle.length === 1) {
                            clientID = clientIDEle[0].value
                        } else if(clientEle.length === 1) {
                            clientID = clientEle[0].value
                        } else if ("%(name)s".includes("_set_")) {
                            let SN = "%(name)s".split("_set_");
                            if (SN.length == 2) {
                                let ILN = SN[1].split("_", 1);
                                if (ILN && !isNaN(ILN)) {
                                    let clName = SN[0] + "_set-" + ILN[0] + "-client";
                                    let clEle = django.jQuery('input[name=' + clName + ']');
                                    if (clEle.length === 1) {
                                        clientID = clEle[0].value
                                    }
                                }
                            }
                        }

                        return {
                            search_fields: '%(search_fields)s',
                            app_label: '%(app_label)s',
                            model_name: '%(model_name)s',
                            extraQSFilter: '%(extra_qs_filter)s',
                            numItems: '%(num_items)s',
                            clientID: clientID,
                            clientCompanyID: clientCompanyID
                        }
                    }

                });
            </script>
        """
        html = mark_safe(raw_html) % {
            'search_fields': ','.join(self.search_fields),
            'model_name': self.model_name,
            'app_label': self.app_label,
            'label': '',
            'name': name.replace('-', '_'),
            'value': value,
            'selected': selected,
            "extra_qs_filter": self.extra_qs_filter,
            "num_items": self.num_items,
            'help_text': self.help_text
        }
        return html

    def render(self, name, value, attrs=None, renderer=None):
        selected = ''
        if value:
            if self.array_value_separator:
                value = value.split(self.array_value_separator)
        else:
            value = []

        related_qs = self.get_related_query_set(value)

        for obj in related_qs:
            extra_value_string = ""
            if self.sub_labels:
                extra_values = []
                for field in self.sub_labels:
                    extra_label = ''
                    sub_labels_values = field.split('.')

                    for index, sub_field in enumerate(sub_labels_values):
                        if index == 0:
                            extra_label = getattr(obj, sub_field)
                        elif extra_label:
                            extra_label = getattr(extra_label, sub_field)

                        if type(extra_label) == str:
                            extra_values.append(' (%s) ' % extra_label)

                extra_value_string = ''.join(extra_values)
            selected += self.get_display_html(obj, name, extra_value_string)

        if self.is_readonly:
            return selected

        return self.get_autocomplete_html(name, value, selected)


