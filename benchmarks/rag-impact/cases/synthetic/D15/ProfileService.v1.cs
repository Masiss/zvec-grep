namespace Synthetic.Stale
{
    public class ProfileService
    {
        public void UpdateName(string name)
        {
            var current = Load();
            current.FullName = name;
            Save(current);
        }
    }
}
