
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import GitlabUserBase
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
          'keytitle': (list(string_types)),
          'pubkey': (list(string_types)),

# TODO: handle state == absent aka key delete
##          'state': (list(string_types), 'present'),
        })

        return tmp


    def run_specific(self, result):
        usr = self.gitlab_user

        # check if sshkey already exists
        sshkey = None
        keytitle = self.get_taskparam('keytitle')
        keycontent = self.get_taskparam('pubkey')

        display.vv(
          "ActionModule[run] :: Check if ssh key already exists"\
          " with given title '{}'".format(keytitle)
        )

        for k in usr.keys.list():
            if k.title == keytitle:
                display.vv("ActionModule[run] :: Found existing key")
                sshkey = k

        if sshkey:
            if sshkey.key == keycontent:
                # nothing changed, so nothing todo
                return result

            display.vv("ActionModule[run] :: New and old key differ, update key")
            # key already exists, but it changed, as gitlab API 
            # seems not to support direct update, we will delete 
            # it first and than readd it
            sshkey.delete()

        # if we get to this point, we have either a completly new 
        # key or an already existing one which has changed, in 
        # any case we need to add the key
        display.vv(
          "ActionModule[run] :: Add new key: {}".format(keycontent)
        )

        usr.keys.create({
            'title': keytitle,
            'key': keycontent,
        })

        result['changed'] = True

        ##usr.save()

        return result

