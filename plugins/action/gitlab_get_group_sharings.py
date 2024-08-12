
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import \
    GitlabBase,\
    get_gitlab_rolemapping

from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible.utils.display import Display


display = Display()


class ActionModule(GitlabBase):

    def __init__(self, *args, **kwargs):
        super(ActionModule, self).__init__(*args, **kwargs)

        self._supports_check_mode = False
        self._supports_async = False


    @property
    def argspec(self):
        tmp = super(ActionModule, self).argspec

        tmp.update({
          'group_id': (list(string_types)),

          'optional': ([bool], False),

          'inverted': ([bool], False),
          'ignores': ([list(string_types)], []),
        })

        return tmp


    def _check_to_ignore(self, grp, ignores):
        ##
        ## check if this membership is on ignore list
        ##
        to_ignore = False

        for ign in ignores:
            try:

                ## if given ignore value is intable assume object
                ## id and compare against that
                if int(ign) == grp['id']:
                    to_ignore = True

            except ValueError:

                ## if given ignore value is no int assume full_path
                ## and test that
                if ign == grp['full_path']:
                    to_ignore = True

        if to_ignore:
            display.vv(
               "ActionModule[_check_to_ignore] :: ignore"\
               " existing membership: {}({}) / {}".format(
                   grp['full_path'], grp['id'], grp['access_level']
               )
            )

        return to_ignore


    def _norm_shared_group(self, grp):
        role_mappings = get_gitlab_rolemapping(invert=True)
        keypfx = 'group_'

        ## normalize keys
        new_x = {}

        for k, v in grp.items():
            if k.startswith(keypfx):
                k = k[len(keypfx):]

            new_x[k] = v

        new_x['access_level'] = role_mappings[new_x['access_level']]
        return new_x


    def run_specific(self, result):
        mygrp_id = self.get_taskparam('group_id')
        mygrp = self.get_group_by_id(mygrp_id,
           non_exist_okay=self.get_taskparam('optional')
        )

        ignores = self.get_taskparam('ignores')
        inverted = self.get_taskparam('inverted')

        result['inverted'] = inverted

        if not mygrp:
            ##
            ## optional group for given id does not exist, as this
            ##   is explicitly allowed by setting the optional
            ##   flag, no error
            ##
            result['group'] = None
            result['group_sharings'] = {}
            return result

        result['group'] = mygrp.full_path

        grpshares = {}

        if inverted:
            ##
            ## in inverted mode we check for inverted/reversed group
            ## sharing where current group is sharee instead of sharer
            ##
            ## note: for now this is a rather expensive operation as
            ##   we dont know a better trick yet as to iterate through
            ##   all groups on the server
            ##
            for g in self.gitlab_client.groups.list(iterator=True):
                if g.id == mygrp.id:
                    ## no need to check against myself :)
                    continue

                g = self.gitlab_client.groups.get(g.id)

                for x in g.shared_with_groups:
                    new_x = self._norm_shared_group(x)

                    if int(new_x['id']) != mygrp.id:
                        ## we only care about sharings where
                        ## mygrp is sharee here
                        continue

                    new_x['id'] = g.id
                    new_x['full_path'] = g.full_path
                    new_x['name'] = g.name

                    if self._check_to_ignore(new_x, ignores):
                        continue

                    grpshares[g.full_path] = new_x

                    break

        else:

            for x in mygrp.shared_with_groups:
                new_x = self._norm_shared_group(x)

                if self._check_to_ignore(new_x, ignores):
                    continue

                grpshares[new_x['full_path']] = new_x

        result['group_sharings'] = grpshares
        return result

