
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.action_base import BaseAction
from ansible_collections.smabot.base.plugins.module_utils.plugins.plugin_base import MAGIC_ARGSPECKEY_META

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert
from ansible.utils.display import Display


display = Display()


class GitlabBase(BaseAction):

    def __init__(self, *args, **kwargs):
        super(GitlabBase, self).__init__(*args, **kwargs)
        self._gitlab_client = None


    @property
    def argspec(self):
        tmp = super(GitlabBase, self).argspec

        tmp.update({
          MAGIC_ARGSPECKEY_META: {
             'mutual_exclusions': [
                ['api_token', 'api_username'],
                ['api_token', 'api_password'],
             ],
          },

          'api_url': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auh_gitlab_url'],
##         'env': '',
            },
          },

          'api_token': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_token'],
               'fallback': ''
            },
          },

          'api_username': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_user'],
               'fallback': ''
            },
          },

          'api_password': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_pw'],
               'fallback': ''
            },
          },

          'validate_certs': {
            'type': [bool],
            'defaulting': {
               'ansvar': ['auth_gitlab_certval'],
               'fallback': True
            },
          },
        })

        return tmp


    @property
    def gitlab_url(self):
        return self.get_taskparam('api_url')

    @property
    def gitlab_auth_token(self):
        return self.get_taskparam('api_token')

    @property
    def gitlab_client(self):
        return self.get_server_client()


    def get_server_client(self, re_auth=False, **kwargs):
        if not self._gitlab_client or re_auth:

            if not self._gitlab_client:
                display.vvv("GitLabBase :: Initial client creation and authing")
            else:
                display.vvv("GitLabBase :: re-authing")

            tmp = {
              'url': self.gitlab_url,
              'ssl_verify': self.get_taskparam('verify_ssl'), 
              'private_token': self.gitlab_auth_token,
              'api_version': 4
            }

            tmp.update(kwargs)
            tmp = gitlab.Gitlab(**tmp)
            tmp.auth()

            self._gitlab_client = tmp

        return self._gitlab_client


    def re_auth(self, **kwargs):
        ## note: it seems that because of internal caching (I assume) 
        ##   it is not possible to re-auth with the same client instance 
        ##   (or I simply dont get how to do it right atm), we solve 
        ##   this issue for now be creating a new client instance
        self.get_server_client(re_auth=True, **kwargs)


    def exec_gitlab_module(self, modname, modargs=None, **kwargs):
        modargs = modargs or {}

        for ap in ['api_url', 'validate_certs']:
            modargs.setdefault(ap, self.get_taskparam(ap))

        for ap in ['api_username', 'api_password', 'api_token']:
            tmp = self.get_taskparam(ap)

            if tmp:
                modargs.setdefault(ap, tmp)

        return self.exec_module(modname, modargs=modargs, **kwargs)


class GitlabUserBase(GitlabBase):

    def __init__(self, *args, **kwargs):
        super(GitlabUserBase, self).__init__(*args, **kwargs)
        self._glusr = None


    @property
    def argspec(self):
        tmp = super(GitlabUserBase, self).argspec

        tmp.update({
          'user': (list(string_types)),
        })

        return tmp


    @property
    def gitlab_user(self):
        return self.get_glusr()


    def get_glusr(self, forced_reload=False):
        if not self._glusr or forced_reload:
            usrname = self.get_taskparam('user')

            # get user object
            display.vv(
              "GitlabUserBase :: Querying gitlab user for"\
              " given name '{}'".format(usrname)
            )

            tmp = self.gitlab_client.users.list(username=usrname)

            if not tmp: 
                return AnsibleError(
                  "Could not find a gitlab user named '{}'".format(usrname)
                )

            if len(tmp) > 1: 
                return AnsibleAssertionError(
                  "Found more than one user matching given" \
                  " name '{}'".fromat(usrname)
                )

            self._glusr = tmp[0]
            display.vv("GitlabUserBase :: Found user on gitlab")

        return self._glusr


    def re_auth(self, **kwargs):
        super(GitlabUserBase, self).re_auth(**kwargs)

        ## note: as re-authing atm makes it necessary to replace 
        ##   the client instance with a new one we also need to 
        ##   force a reload here of the user object
        self.get_glusr(forced_reload=True)

