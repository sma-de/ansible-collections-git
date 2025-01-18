
from __future__ import (absolute_import, division, print_function)
__metaclass__ = type


import collections
import uuid
import re

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

    TRANSFERS_FILES = False
    _requires_connection = False


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
          'enable_token_version_mode': ([bool], True),

          ## must match together with matcher regex
          'token_version_suffix': (list(string_types), "-ver%Y%m%d-%H%M%S"),

          'token_version_matcher': (list(string_types),
              ##
              ## must always contain 3 groups, one matching all prefix
              ## text, one matching the complete versioned meta extra
              ## text, one matching the suffix
              ##
              "(.*?)(-ver\d{8}-\d{6})(.*?)"
          ),
        })

        return tmp


    def configure_token_maxdate(self, cfg, days=EXPIRE_MAX_DAYS):
        import datetime

        expires = datetime.datetime.now()\
                + datetime.timedelta(days=EXPIRE_MAX_DAYS)

        expires = expires.strftime('%Y-%m-%d')
        cfg['expires_at'] = expires


    def _create_new_usrtoken(self, usr, cfg,
       result=None, rescol=None, state=None, extra_meta=None, **kwargs
    ):
        if not isinstance(cfg, collections.abc.Mapping):
            ## assume simple true/false bool
            cfg = {}

        if extra_meta is None:
            extra_meta = {}

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
                self.configure_token_maxdate(cfg)

        else:
            ansible_assert(False,
               "unsupported token type '{}', must be one"\
               " of these: {}".format(tktype, ['pat', 'impersonate'])
            )

        do_tokvers = extra_meta.get('versioned', False)
        tver_sfx = self.get_taskparam('token_version_suffix')
        origname = cfg['name']

        if do_tokvers:
            ## create "versioned" token by auto adding version
            ## suffix to given name
            import datetime

            cfg['name'] += datetime.datetime.now().strftime(tver_sfx)
            extra_meta['versioned'] = True
            tmp = extra_meta.setdefault('vertokens', {'active': []})
            tmp['active'].insert(0, cfg['name'])

        tmp = tk_create_fn(cfg)

        if rescol is not None:
            mdata = tmp.asdict()
            mdata.pop('token')

            rescol[state][origname] = {
              'token': tmp.token, 'metadata': mdata, 'type': tktype
            }

            rescol[state][origname].update(extra_meta)

        if result is not None:
            result['changed'] = True

        return tmp


    def run_specific(self, result):
        usr = self.gitlab_user

        state = self.get_taskparam('state')
        exclusive = self.get_taskparam('exclusive')

        ena_tokvers = self.get_taskparam('enable_token_version_mode')
        tver_sfx = self.get_taskparam('token_version_suffix')
        tver_matcher = self.get_taskparam('token_version_matcher')

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
            temp_token = {'name': str(uuid.uuid4()), 'scopes': ['api', 'admin_mode']}
            self.configure_token_maxdate(temp_token, days=1)

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
                 self.gitlab_client.personal_access_tokens.list(
                   user_id=usr.id, state='active',
                 )
            ))

        display.vv(
           "ActionModule[run_specific] :: handle existing"\
           " tokens: {}".format(list(map(lambda x: x.name, tklist)))
        )

        # handle existing tokens
        imp_tokens_cache = {}

        ## build impersonating token cache
        for imptk in usr.impersonationtokens.list(state='active'):
            tkname = imptk.name

            ## extract base name for versioned tokens
            m = re.fullmatch(tver_matcher, tkname)

            if m:
                pre, ver, sfx = m.group(1, 2, 3)
                tkname = pre + sfx

            imp_tokens_cache[tkname] = imptk

        ## group versioned token together as one unity
        if ena_tokvers:
            newlst = {}

            for ut in tklist:
                ## check if name of token matches the current
                ## versioning pattern
                m = re.fullmatch(tver_matcher, ut.name)

                if not m:
                    newlst[ut.name] = {'versioned': False, 'tokens': [ut]}
                    continue

                ## extract basename
                pre, ver, sfx = m.group(1, 2, 3)
                utn = pre + sfx

                ## combine with other tokens of same version group
                tmp = newlst.setdefault(utn,
                  {'versioned': True, 'tokens': []}
                )

                tl = tmp['tokens']
                tl.append(ut)
                tl.sort(key=lambda x: x.name)

                tmp['tokens'] = list(reversed(tl))

            tklist = newlst

        else:

            newlst = {}

            for ut in tklist:
                newlst[ut.name] = [ut]

            tklist = newlst

        for utname, tkmeta in tklist.items():
            tokens = tkmeta['tokens']

            display.vv(
              "ActionModule[run_specific] :: handle existing"\
              " token set '{}'".format(list(map(lambda x: x.name, tokens)))
              ##" token '{}'".format(utname)
            )

            extra_meta = {}

            if tkmeta['versioned']:
                ansible_assert(ena_tokvers,
                    "Found versioned token set for basename '{}',"\
                    " but versioning is currently"\
                    " disabled: {}".format(utname, tokens)
                )

                extra_meta['versioned'] = True
                vtoks = []

                for x in tokens:
                    vtoks.append(x.name)

                extra_meta['vertokens'] = { 'active': vtoks }

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
                    token_col['removed'][utname].update(extra_meta)
                    result['changed'] = True

                    for ut in tokens:
                        ut.delete()

                else:

                    token_col['unchanged'][utname] = {
                      'metadata': ut.asdict(), 'type': deftype
                    }

                    token_col['unchanged'][utname].update(extra_meta)

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

                token_col['unchanged'][utname].update(extra_meta)
                continue

            versions = newcfg.pop('versions', None)
            vc = None

            if versions:
                if not isinstance(versions, collections.abc.Mapping):
                    ## assume simple int for version count
                    versions = { 'count': versions }

                ansible_assert(ena_tokvers,
                    "User token with name '{}' is configured to be"\
                    " versioned, but versioning was globally disabled"\
                    " by flag".format(utname)
                )

                if tkmeta['versioned']:
                    ##
                    ## if current token was already versioned, we adher
                    ## to count, if we switched for a previously
                    ## unversioned token to versioned we must remove
                    ## all existing ones
                    ##
                    vc = versions.get('count', None)

                    ansible_assert(vc,
                        "Must give a version count when using token"\
                        " versioning for token '{}'".format(utname)
                    )

                    vc = int(vc)

                    ansible_assert(vc > 0,
                        "Given version count must be a positive integer"\
                        " number greater zero but was '{}'".format(vc)
                    )

                extra_meta['versioned'] = True
            else:
                ##
                ## for the potential case where we switch from a versioned
                ## token to a "normal" unversioned token make sure meta
                ## data is correct
                ##
                extra_meta.pop('versioned', None)
                extra_meta.pop('vertokens', None)

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

                deltoks = tokens

                if vc:
                    deltoks = deltoks[(vc - 1):]

                rmtoks = []

                for ut in deltoks:
                    rmtoks.append(ut.name)
                    ut.delete()

                if versions:
                    extra_meta.setdefault('vertokens', {}).update(removed=rmtoks)

                    new_active = []

                    for x in extra_meta['vertokens'].get('active', []):
                        if x not in rmtoks:
                            new_active.append(x)

                    extra_meta['vertokens']['active'] = new_active

                display.vvv(
                  "ActionModule[run_specific] :: update token recreate"
                )

                self._create_new_usrtoken(
                   usr, newcfg, result, token_col, name=utname, 
                   state='updated', extra_meta=extra_meta,
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
                token_col['removed'][utname].update(extra_meta)

                for ut in tokens:
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

            extra_meta = {}
            versions = cfg.pop('versions', None)

            if versions:
                ansible_assert(ena_tokvers,
                    "User token with name '{}' is configured to be"\
                    " versioned, but versioning was globally disabled"\
                    " by flag".format(name)
                )

                extra_meta['versioned'] = True
                extra_meta['vertokens'] = {'active': []}

            self._create_new_usrtoken(usr, cfg, result, token_col, 
              name=name, state='created', extra_meta=extra_meta,
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

