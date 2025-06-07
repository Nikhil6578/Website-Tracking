
from functools import update_wrapper

from django.conf import settings
from django.contrib.admin.sites import AdminSite
from django.template.response import TemplateResponse
from django.urls import include, path, re_path
from django.utils.functional import LazyObject
from django.views.decorators.cache import never_cache

from contify.website_tracking.constants import WST_ADMIN_PREFIX_URL, WST_PATH


WST_ADMIN_ASSET_URL_PREFIX = f"{settings.MEDIA_URL}{WST_PATH}"


class WstAdminSite(AdminSite):
    index_template = "wst_admin/index.html"
    app_index_template = "wst_admin/app_index.html"

    def get_urls(self):
        def wrap(view, cacheable=False):
            def wrapper(*args, **kwargs):
                return self.admin_view(view, cacheable)(*args, **kwargs)
            wrapper.admin_site = self
            return update_wrapper(wrapper, view)

        # Admin-site-wide views.
        urlpatterns = [
            path('', wrap(self.index), name='index'),
            path('jsi18n/', wrap(self.i18n_javascript, cacheable=True), name='jsi18n'),
            path('password_change/', wrap(self.password_change, cacheable=True), name='password_change'),
            path('login/', self.login, name='login'),
            path('logout/', wrap(self.logout), name='logout')
        ]

        # Add in each model's views, and create a list of valid URLS for the
        # app_index
        valid_app_labels = []
        for model, model_admin in self._registry.items():
            urlpatterns += [
                path('%s/%s/' % (model._meta.app_label, model._meta.model_name), include(model_admin.urls)),
            ]
            if model._meta.app_label not in valid_app_labels:
                valid_app_labels.append(model._meta.app_label)

        # If there were ModelAdmins registered, we should have a list of app
        # labels for which we need to allow access to the app_index view,
        if valid_app_labels:
            regex = r'^(?P<app_label>' + '|'.join(valid_app_labels) + ')/$'
            urlpatterns += [
                re_path(regex, wrap(self.app_index), name='app_list'),
            ]

        return urlpatterns

    @never_cache
    def index(self, request, extra_context=None):
        """
        Display the main admin index page, which lists all of the installed
        apps that have been registered in this site.
        """
        # app_list = self.get_app_list(request)
        app_list = [
            {
                'name': 'Curation',
                'app_label': 'web_snapshot',
                # 'app_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/',
                'has_module_perms': True,
                'models': [
                    {
                        'name': 'Diff contents',
                        'object_name': 'DiffContent',
                        'perms': {
                            # 'add': True,
                            'change': True,
                            # 'delete': True,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/diffcontent/',
                        # 'add_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/diffcontent/add/',
                        'view_only': False
                    }
                ]
            },

            {
                'name': 'Web_Sourcing',
                'app_label': 'website_tracking',
                # 'app_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/',
                'has_module_perms': True,
                'models': [
                    {
                        'name': 'Web sources',
                        'object_name': 'WebSource',
                        'perms': {
                            'add': True,
                            'change': True,
                            'delete': True,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/websource/',
                        'add_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/websource/add/',
                        'view_only': False
                    },
                    {
                        'name': 'Web client sources',
                        'object_name': 'WebClientSource',
                        'perms': {
                            'add': True,
                            'change': True,
                            'delete': True,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/webclientsource/',
                        'add_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/webclientsource/add/',
                        'view_only': False
                    },
                ]
            },

            {
                'name': 'Website_Update (Story)',
                'app_label': 'website_tracking',
                # 'app_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/',
                'has_module_perms': True,
                'models': [
                    {
                        'name': 'Web updates',
                        'object_name': 'WebUpdate',
                        'perms': {
                            'add': False,
                            'change': True,
                            'delete': False,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/website_tracking/webupdate/',
                        'add_url': None,
                        'view_only': False
                    },

                ]
            },

            {
                'name': 'Web_Snapshot',
                'app_label': 'web_snapshot',
                # 'app_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/',
                'has_module_perms': True,
                'models': [
                    {
                        'name': 'Diff htmls',
                        'object_name': 'DiffHtml',
                        'perms': {
                            'add': False,
                            'change': False,
                            'delete': False,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/diffhtml/',
                        'add_url': None,
                        'view_only': True
                    },
                    {
                        'name': 'Web snapshots',
                        'object_name': 'WebSnapshot',
                        'perms': {
                            'add': False,
                            'change': False,
                            'delete': False,
                            'view': True
                        },
                        'admin_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/websnapshot/',
                        # 'add_url': f'{WST_ADMIN_PREFIX_URL}/admin/web_snapshot/websnapshot/add/',
                        'view_only': True
                    }
                ]
            }
        ]

        context = {
            **self.each_context(request),
            'title': self.index_title,
            'app_list': app_list,
            **(extra_context or {}),
        }

        request.current_app = self.name

        return TemplateResponse(request, self.index_template or 'admin/index.html', context)


class WstDefaultAdminSite(LazyObject):
    def _setup(self):
        self._wrapped = WstAdminSite()


cfy_admin_site = WstDefaultAdminSite()

EXTRA_HEAD = {
    "js": (
        f"{WST_ADMIN_ASSET_URL_PREFIX}/admin/js/jquery-ui-1.12.1.min.js",
        f"{WST_ADMIN_ASSET_URL_PREFIX}/admin/js/diff_content_change_list.js"
    ),
    "css": (
        f"{WST_ADMIN_ASSET_URL_PREFIX}/admin/css/autocomplete.css",
        f"{WST_ADMIN_ASSET_URL_PREFIX}/admin/css/diff_content_change_list.css"
    )
}



