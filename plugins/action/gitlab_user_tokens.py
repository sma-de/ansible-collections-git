
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections
import uuid

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.gitlab.plugins.module_utils.plugins.gitlab_action import GitlabUserBase
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
          'user_tokens': ([collections.abc.Mapping]),
          'state': (list(string_types), 'present', ['present', 'absent', 'update']),
          'strict': ([bool], False),
        })

        return tmp


    def _create_new_usrtoken(self, usr, cfg, 
       result=None, rescol=None, state=None, **kwargs
    ):
        cfg.update(kwargs)

        tmp = usr.impersonationtokens.create(cfg)

        if rescol is not None:
            rescol[tmp.name] = { 'token': tmp.token, 'state': state }

        if result is not None:
            result['changed'] = True

        return tmp


    def run_specific(self, result):
        usr = self.gitlab_user

        state = self.get_taskparam('state')
        strict = self.get_taskparam('strict')

        usrtoks = self.get_taskparam('user_tokens')

        token_col = {}

        temp_token = None
        new_authtoken = None

        ## note: important to query the list before we might create 
        ##   a temp token as we obviously dont want to handle that
        tklist = usr.impersonationtokens.list(state='active')

        ##
        ## note: if the user where we mess with the tokens is the 
        ##   same user we authed with, there is a good chance that 
        ##   we update the same token we used as auth, so removing 
        ##   the same access way we currently use for managing, to 
        ##   avoid this we create a temporary acces token to make 
        ##   sure we have access to the server all the time
        ##
        if self.gitlab_client.user.username == usr.username:

            temp_token = {'name': str(uuid.uuid4()), 'scopes': ['api']}

            display.vv(
              "ActionModule[run_specific] :: user to handle"\
              " is auth user, create temp token: {}".format(temp_token)
            )

            ## note: to avoid name clashes with existing tokens use uuid here
            temp_token = self._create_new_usrtoken(usr, temp_token)

            display.vv(
              "ActionModule[run_specific] :: re-auth with temp token"
            )

            self.re_auth(private_token=temp_token.token)

            ## note: unfortunately this simple straigtforward way 
            ##   of re-authing with a new token does not work
            ##self.gitlab_client.private_token = temp_token.token
            ##self.gitlab_client.auth()

            ## note: all these objects still reference the old token 
            ##   after re-authing, we need to reset them to the new 
            ##   token explicitly
            usr = self.gitlab_user

            temp_token = next(filter(
               lambda x: x.name == temp_token.name, 
               usr.impersonationtokens.list(state='active')
            ))

        display.vv(
           "ActionModule[run_specific] :: handle existing"\
           " tokens: {}".format(list(map(lambda x: x.name, tklist)))
        )

        # handle existing tokens
        for ut in tklist:
            utname = ut.name

            display.vv(
              "ActionModule[run_specific] :: handle existing"\
              " token '{}'".format(utname)
            )

            newcfg = usrtoks.pop(utname, None)

            if not newcfg:
                # user token existing in gitlab not mentioned by 
                # ansible config, when in strict mode delete it, 
                # otherwise do nothing
                if strict:
                    token_col[utname] = { 'state': 'strict_removed' }
                    result['changed'] = True
                    ut.delete()
                continue

            display.vv(
              "ActionModule[run_specific] :: found ansible config"\
              " for current token: {}".format(newcfg)
            )

            # handle existing user token in gitlab also mentioned 
            # by ansible config depending on set state
            if state == 'update':
                display.vv(
                  "ActionModule[run_specific] :: do a token update"
                )

                # update is not really a thing in gitlab api, so we 
                # actually do a delete and recreate combo
                display.vvv(
                  "ActionModule[run_specific] :: update token delete"
                )

                ut.delete()

                display.vvv(
                  "ActionModule[run_specific] :: update token recreate"
                )

                self._create_new_usrtoken(
                   usr, newcfg, result, token_col, name=utname, 
                   state='updated'
                )

                display.vvv(
                  "ActionModule[run_specific] :: update token done"
                )

                continue

            if state == 'absent':
                display.vv(
                  "ActionModule[run_specific] :: do a token delete"
                )

                token_col[utname] = { 'state': 'absent_removed' }
                ut.delete()
                result['changed'] = True
                continue

            # note: state == 'present' is a noop here
            display.vv(
              "ActionModule[run_specific] :: existing token unchanged" 
            )

        display.vv(
           "ActionModule[run_specific] :: handle new"\
           " tokens: {}".format(list(usrtoks.keys()))
        )

        # handle new tokens
        for name, cfg in usrtoks.items():
            self._create_new_usrtoken(usr, cfg, result, token_col, 
              name=name, state='created'
            )

        ##usr.save()

        if temp_token:
            display.vv(
              "ActionModule[run_specific] :: remove temp token again"
            )

            temp_token.delete()

        result['user_tokens'] = token_col
        return result

