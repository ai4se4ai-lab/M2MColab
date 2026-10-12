class Wallet:
    """A wallet that holds money in cents."""

    def __init__(self):
        self.cents = 0

    def deposit(self, cents):
        """
        Add money to the wallet.
        :param cents: int, must be positive
        :return: int, the new balance
        >>> w = Wallet()
        >>> w.deposit(250)
        250
        """
        pass

    def withdraw(self, cents):
        """
        Remove money; raise ValueError if the balance would go below zero.
        :return: int, the new balance
        >>> w = Wallet(); _ = w.deposit(300)
        >>> w.withdraw(120)
        180
        """
        pass
