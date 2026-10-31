"""Read the authenticated private metrics snapshot without starting a service."""
import sys
from runner.cli import request_control
from runner.worker import ConfigurationError


def main():
    response=request_control({'action':'metrics'})
    result=response.get('result')
    if response['ok'] is not True or not isinstance(result,dict) or set(result)!={'metrics'} or not isinstance(result['metrics'],str): raise ConfigurationError('metrics unavailable')
    sys.stdout.write(result['metrics'])
    return 0


if __name__=='__main__':
    try: sys.exit(main())
    except (OSError,ValueError,ConfigurationError):
        print('Metrics request failed.',file=sys.stderr); sys.exit(1)
