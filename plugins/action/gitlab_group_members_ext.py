
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections
import uuid

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import \
  GitlabBase, \
  get_gitlab_rolemapping

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()



##
## this class basically wraps upstream module "community.general.gitlab_user"
## and adds some extra functionality upstream module is (currently) lacking
## like handling "service_accounts"
##
class ActionModule(GitlabBase):

    UPSTREAM_USER_MODULE = 'community.general.gitlab_group_members'

    UPSTREAM_FORWARDING_PARAMS = [
      'gitlab_group', 'purge_users', 'gitlab_users_access', 'state',
    ]


    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          'gitlab_groups_access': ([[collections.abc.Mapping]], []),
          'purge_groups': ([[string_types]], []),

          ## upstream forwarding params
          'gitlab_group': (list(string_types)),

          'gitlab_users_access': ([[collections.abc.Mapping]] + [type(None)], None),
          'purge_users': ([[string_types]] + [type(None)], None),

          'state': (list(string_types), 'present'),
        })

        return tmp


    def _create_new_service_account(self, result):
        uname = self.get_taskparam('username')

        ##
        ## note: python-gitlab lib seems not to support service accounts
        ##   directly in high-level manor, but we still can get access
        ##   to "raw" gitlab api by using its low-level-api feature:
        ##
        ##     https://python-gitlab.readthedocs.io/en/stable/api-levels.html#lower-level-apis
        ##
        display.vv(
          "ActionModule[_create_new_service_account] :: calling"\
          " server rest api to create a new service account"
        )

        tmp = self.gitlab_client.http_post('/service_accounts')

        display.vvv(
          "ActionModule[_create_new_service_account] :: answer from server"\
          " for service account creation rest api request: {}".format(tmp)
        )

        ##
        ## finally change randomly generated service-account username
        ## to parameter requested "real" username, after this step we
        ## can handle/manage a service-account user basically like
        ## any other "normal" user
        ##
        botusr = self.gitlab_client.users.get(tmp['id'])
        botusr.username = uname
        botusr.save()


    def run_specific(self, result):
        ##
        ## do extended pre stuff
        ##

        mygrp = self.get_taskparam('gitlab_group')
        state = self.get_taskparam('state')

        modstate_group_members = {
          'added': {},
          'updated': {},
          'removed': {},
          'unchanged': {},
        }

        presenting = state == 'present'
        absenting = state == 'absent'

        ##
        ## call usptream user management module
        ##
        modargs = {}

        for x in self.UPSTREAM_FORWARDING_PARAMS:
            if x in modargs:
                ## skip internally modified standard args
                continue

            xval = self.get_taskparam(x)

            if xval is not None:
                modargs[x] = xval

        cmdret = self.exec_gitlab_module(
           self.UPSTREAM_USER_MODULE,
           modargs=modargs,
        )

        ##
        ## do extended post stuff
        ##
        result.update(cmdret)

        ##
        ## note: currently upstream module handle
        ##   changed in a weird unstandard way
        ##
        if isinstance(result['changed'], string_types):
            if result['changed'].lower() == 'ok':
                result['changed'] = False

        ##
        ## handle group memberships here as upstream
        ## module currently only supports users
        ##
        member_grps = self.get_taskparam('gitlab_groups_access')
        grp_purge = self.get_taskparam('purge_groups')

        if member_grps or grp_purge:
            mygrp_obj = self.get_group_by_id(mygrp)

            given_grps = []

            for x in member_grps:
                other = self.get_group_by_id(x['id'])
                given_grps.append(other.id)

                role = x['access_level']
                mapped_role = get_gitlab_rolemapping().get(role, None)

                ansible_assert(mapped_role,
                   "invalid access_level '{}' given, must be one of"\
                   " these: {}".format(role,
                      list(get_gitlab_rolemapping().keys())
                   )
                )

                ## check if sharing with right level exists already
                other_shared = self.get_shared_groups(
                   mygrp_obj
                ).get(other.id, None)

                state_grpmem_key = 'added'
                state_grpmem_val = {
                  'role': role,
                }

                if other_shared:
                    if absenting:
                        mygrp_obj.unshare(other.id)
                        modstate_group_members['removed'][other.full_path] =\
                            {'reason': 'absenting'}

                        result['changed'] = True
                        continue

                    if presenting:
                        if other_shared['group_access_level'] == mapped_role:
                            ## group mapping is already like it should be, noop
                            state_grpmem_val['shared'] = True

                            modstate_group_members['unchanged'][other.full_path] =\
                              state_grpmem_val

                            continue

                        ## a group sharing already exists, but with the
                        ## wrong access level, it seems that to change
                        ## access level, group sharing must be
                        ## recreated with new level
                        mygrp_obj.unshare(other.id)
                        state_grpmem_key = 'updated'

                elif absenting:
                    ## absenting noop case
                    modstate_group_members['unchanged'][other.full_path] =\
                        {'shared': False}

                    continue

                if presenting:
                    ## create new/updated grp sharing
                    mygrp_obj.share(other.id, mapped_role)

                    modstate_group_members[state_grpmem_key][other.full_path] =\
                      state_grpmem_val

                    result['changed'] = True

            if grp_purge and presenting:
                ## get all grp sharing and remove all not on explicit list
                for x in mygrp_obj.shared_with_groups:
                    if x['group_id'] not in given_grps:
                        mygrp_obj.unshare(x['group_id'])

                        modstate_group_members['removed'][x['group_full_path']] =\
                            {'reason': 'purging'}

                        result['changed'] = True

        result['group_members'] = modstate_group_members
        return result

