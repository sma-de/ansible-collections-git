
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


from ansible.errors import \
  AnsibleAssertionError,\
  AnsibleError

####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.base.plugins.module_utils.plugins.action_base import BaseAction
from ansible_collections.smabot.base.plugins.module_utils.plugins.plugin_base import MAGIC_ARGSPECKEY_META

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert
from ansible.utils.display import Display


display = Display()


def get_gitlab_rolemapping(invert=False):
  import gitlab

  res = {
    'guest': gitlab.const.AccessLevel.GUEST,
    'reporter': gitlab.const.AccessLevel.REPORTER,
    'developer': gitlab.const.AccessLevel.DEVELOPER,
    'maintainer': gitlab.const.AccessLevel.MAINTAINER,
    'owner': gitlab.const.AccessLevel.OWNER,
  }

  if not invert:
    return res

  ## switch keys and values
  new_res = {}

  for k, v in res.items():
      new_res[v] = k

  return new_res


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
               'ansvar': ['auth_gitlab_url', 'auth_gitserver_url_gitlab', 'auth_gitserver_url'],
##         'env': '',
            },
          },

          'api_token': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_token', 'auth_gitserver_token_gitlab', 'auth_gitserver_token'],
               'fallback': ''
            },
          },

          'api_username': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_user', 'auth_gitserver_user_gitlab', 'auth_gitserver_user'],
               'fallback': ''
            },
          },

          'api_password': {
            'type': list(string_types),
            'defaulting': {
               'ansvar': ['auth_gitlab_pw', 'auth_gitserver_pw_gitlab', 'auth_gitserver_pw'],
               'fallback': ''
            },
          },

          'validate_certs': {
            'type': [bool],
            'defaulting': {
               'ansvar': ['auth_gitlab_certval', 'auth_gitserver_valcerts_gitlab', 'auth_gitserver_valcerts'],
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


    def get_group_by_id(self, gid, non_exist_okay=False):
        try:
            ## case 1: given gid is numerical gitlab
            ##   object id for group, so we can do direct access here
            gid = int(gid)
            return self.gitlab_client.groups.get(gid)
        except ValueError:
            pass

        ## case 2: assume gid is string containing
        ##   fullpath for group, use it to find group
        display.vv(
           "GitLabBase(get_group_by_id) :: search for group by"\
           " its fullpath '{}' ...".format(gid)
        )

        for g in self.gitlab_client.groups.list(iterator=True):
            ##display.vvv(
            ##   "GitLabBase(get_group_by_id) :: examine"\
            ##   " server group: {}".format(g)
            ##)

            if g.full_path == gid:
                display.vv(
                   "GitlabBase(get_group_by_id) :: found group on"\
                   " server matching fullpath '{}'".format(gid)
                )

                ##
                ## note: object returned by list is not necessary as
                ##   complete as a direct group get, so instead of
                ##   returning it directly use it only to get group
                ##   id and return then the result of explicit get call
                ##
                return self.gitlab_client.groups.get(g.id)

        if non_exist_okay:
            return None

        raise AnsibleError(
          "could not find a gitlab group matching fullpath '{}'".format(gid)
        )


    def get_shared_groups(self, group):
        if isinstance(group, string_types + (int,)):
            group = self.get_group_by_id(group)

        ## otherwise assume group is already a proper gitlab group object

        ## make shared groups avaible as mapping instead of default list
        ## which makes working with it easier
        res = {}

        for x in group.shared_with_groups:
            res[int(x['group_id'])] = x
            res[x['group_id']] = x
            res[x['group_full_path']] = x

        return res


    def get_server_client(self, re_auth=False, **kwargs):
        if not self._gitlab_client or re_auth:

            if not self._gitlab_client:
                display.vvv("GitLabBase :: Initial client creation and authing")
            else:
                display.vvv("GitLabBase :: re-authing")

            tmp = {
              'url': self.gitlab_url,
              'ssl_verify': self.get_taskparam('validate_certs'),
              'private_token': self.gitlab_auth_token,
              'api_version': 4
            }

            tmp.update(kwargs)
            import gitlab
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
          'username': (list(string_types)),
        })

        return tmp


    @property
    def gitlab_user(self):
        return self.get_glusr()


    def get_glusr(self, forced_reload=False, non_exist_okay=False):
        if not self._glusr or forced_reload:
            usrname = self.get_taskparam('username')

            # get user object
            display.vv(
              "GitlabUserBase :: Querying gitlab user for"\
              " given name '{}'".format(usrname)
            )

            tmp = self.gitlab_client.users.list(username=usrname)

            if not tmp:
                if non_exist_okay:
                    return None

                raise AnsibleError(
                  "Could not find a gitlab user named '{}'".format(usrname)
                )

            if len(tmp) > 1: 
                raise AnsibleAssertionError(
                  "Found more than one user matching given" \
                  " name '{}'".format(usrname)
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

