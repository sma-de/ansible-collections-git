
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import \
    GitlabUserBase,\
    get_gitlab_rolemapping

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


class ActionModule(GitlabUserBase):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          'ignores': ([list(string_types)], []),
        })

        return tmp


    def run_specific(self, result):
        usr = self.gitlab_user

        ignores = self.get_taskparam('ignores')

        role_mappings = get_gitlab_rolemapping(invert=True)

        memships = {}

        for x in usr.memberships.list():
            x.source_type = x.source_type.lower()

            if x.source_type == 'namespace':
                x.source_type = 'group'

            if x.source_type == 'project':
                xobj = self.gitlab_client.projects.get(x.source_id)
                xobj.full_path = xobj.path_with_namespace
            else:
                ## assume group
                xobj = self.get_group_by_id(x.source_id)

            to_ignore = False

            ##
            ## check if this membership is on ignore list
            ##
            for ign in ignores:
                try:

                    ## if given ignore value is intable assume object
                    ## id and compare against that
                    if int(ign) == xobj.id:
                        to_ignore = True
                        break

                except ValueError:

                    ## if given ignore value is no int assume full_path
                    ## and test that
                    if ign == xobj.full_path:
                        to_ignore = True
                        break

            if to_ignore:
                display.vv(
                   "ActionModule[run_specific] :: ignore"\
                   " existing membership: {}({}) / {}".format(
                      xobj.full_path, xobj.id, role_mappings[x.access_level],
                   )
                )

                continue

            memships[xobj.full_path] = {
              'full_path': xobj.full_path,
              'id': xobj.id,
              'name': xobj.name,
              'type': x.source_type,
              'access_level': role_mappings[x.access_level],
            }

        result['username'] = self.get_taskparam('username')
        result['memberships'] = memships

        return result

