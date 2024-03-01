
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections
import uuid

##from ansible.errors import AnsibleOptionsError, AnsibleModuleError##, AnsibleError
####from ansible.module_utils._text import to_native
from ansible.module_utils.six import iteritems, string_types

from ansible_collections.smabot.git.plugins.module_utils.plugins.gitlab_action import GitlabUserBase
from ansible_collections.smabot.base.plugins.module_utils.utils.utils import ansible_assert

from ansible_collections.smabot.base.plugins.module_utils.utils.dicting import \
  setdefault_none

from ansible.utils.display import Display


display = Display()


##
## note: according to docu this should actually be 365 days currently,
##   but for api call to actually work one must subtract one day
##   from max day num, see also:
##
##     -> https://docs.gitlab.com/ee/user/profile/personal_access_tokens.html#when-personal-access-tokens-expire
##
EXPIRE_MAX_DAYS = 365 - 1


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
          'exclusive': ([bool], False),
        })

        return tmp


    def _create_new_usrtoken(self, usr, cfg, 
       result=None, rescol=None, state=None, **kwargs
    ):
        if not isinstance(cfg, collections.abc.Mapping):
            ## assume simple true/false bool
            cfg = {}

        cfg.update(kwargs)

        expires = cfg.get('expires_at', None)

        tktype = cfg.pop('type', None) or 'pat'

        tk_create_fn = usr.personal_access_tokens.create

        if tktype == 'pat':
            pass

        elif tktype == 'impersonate':
            tk_create_fn = usr.impersonationtokens.create

            if not expires:
                ##
                ## note: for some strange reason and in difference
                ##   to "normal" PATs where "expires_at" is optional
                ##   and gitlab internally defaulted to maximal
                ##   allowed value it is required for impersonating
                ##   tokens, so we default it here to maximal value
                ##
                ## NOTE: there is a chance this defaulting fails if
                ##   the gitserver instance has a custom configured
                ##   lower max value
                ##
                import datetime

                expires = datetime.datetime.now()\
                        + datetime.timedelta(days=EXPIRE_MAX_DAYS)

                expires = expires.strftime('%Y-%m-%d')
                cfg['expires_at'] = expires

        else:

            ansible_assert(False,
               "unsupported token type '{}', must be one"\
               " of these: {}".format(tktype, ['pat', 'impersonate'])
            )

        tmp = tk_create_fn(cfg)

        if rescol is not None:
            mdata = tmp.asdict()
            mdata.pop('token')

            rescol[state][tmp.name] = {
              'token': tmp.token, 'metadata': mdata, 'type': tktype
            }

        if result is not None:
            result['changed'] = True

        return tmp


    def run_specific(self, result):
        usr = self.gitlab_user

        state = self.get_taskparam('state')
        exclusive = self.get_taskparam('exclusive')

        usrtoks = self.get_taskparam('user_tokens')

        token_col = {
          'unchanged': {},
          'removed': {},
          'created': {},
          'updated': {},
        }

        temp_token = None
        new_authtoken = None

        ##
        ## note: important to query the list before we might create
        ##   a temp token as we obviously dont want to handle that
        ##
        ## note.2: this api call actually returns a combination
        ##   of "pure" PATs and also all impersonating tokens,
        ##   which is actually exactly what we need here
        ##
        ## note.3: setting state=active is important here, otherwise
        ##   list will also return some (all??) revoked tokens
        ##   which seem to be kept around somehow
        ##
        tklist = self.gitlab_client.personal_access_tokens.list(
          user_id=usr.id, state='active',
        )

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

            ##
            ## note: all these objects still reference the old token 
            ##   after re-authing, we need to reset them to the new 
            ##   token explicitly
            ##
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
        imp_tokens_cache = {}

        ## build impersonating token cache
        for imptk in usr.impersonationtokens.list(state='active'):
            imp_tokens_cache[imptk.name] = imptk

        for ut in tklist:
            utname = ut.name

            display.vv(
              "ActionModule[run_specific] :: handle existing"\
              " token '{}'".format(ut)
              ##" token '{}'".format(utname)
            )

            newcfg = usrtoks.pop(utname, None)

            ## check real type of existing token,
            ## is it a PAT or an impersonating one??
            deftype = 'pat'

            if utname in imp_tokens_cache:
                deftype = 'impersonate'


            if not newcfg:
                # user token existing in gitlab not mentioned by 
                # ansible config, when in exclusive mode delete it, 
                # otherwise do nothing
                if exclusive:
                    token_col['removed'][utname] = { 'reason': 'exclusive' }
                    result['changed'] = True
                    ut.delete()
                else:
                    token_col['unchanged'][utname] = {
                      'metadata': ut.asdict(), 'type': deftype
                    }

                continue

            display.vv(
              "ActionModule[run_specific] :: found ansible config"\
              " for current token: {}".format(newcfg)
            )

            ## note: for already existing tokens default
            ##   type to the one the current iteration has
            setdefault_none(newcfg, 'type', deftype)

            ##
            ## note: each token can have its own independend state
            ##   setting, if not set fallback to module global one
            ##
            tkstate = newcfg.pop('state', None) or state

            if tkstate == 'present':
                token_col['unchanged'][utname] = {
                  'metadata': ut.asdict(), 'type': newcfg['type']
                }

                continue

            # handle existing user token in gitlab also mentioned 
            # by ansible config depending on set state
            if tkstate == 'update':
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

            if tkstate == 'absent':
                display.vv(
                  "ActionModule[run_specific] :: do a token delete"
                )

                token_col['removed'][utname] = { 'reason': 'absenting' }
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
            tkstate = cfg.pop('state', None) or state

            if tkstate == 'absent':
                continue

            self._create_new_usrtoken(usr, cfg, result, token_col, 
              name=name, state='created'
            )

        ##usr.save()

        if temp_token:
            display.vv(
              "ActionModule[run_specific] :: remove temp token again"
            )

            temp_token.delete()

        tokens_lst_by_name = {}

        for tstates, v in token_col.items():
            for tx, vx in v.items():
                vx['state'] = tstates
                tokens_lst_by_name[tx] = vx

        result['user_tokens'] = {
          'by_state': token_col,
          'by_name': tokens_lst_by_name,
        }

        token_col['value_changed'] = {}
        token_col['value_changed'].update(token_col['created'])
        token_col['value_changed'].update(token_col['updated'])

        return result

